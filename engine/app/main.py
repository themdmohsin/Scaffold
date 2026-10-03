"""Scaffold engine — FastAPI backend.

Frozen contracts live in docs/API_CONTRACTS.md and docs/SCHEMA.md.
Day 1: /health, POST /projects, GET /projects/:id/context, tasks CRUD.
Day 2: + github-webhook ingestion, + MCP server mounted at /mcp.
Day 9 (deployment hardening): central config validation, structured logging with
secret redaction, request-id + rate-limit middleware, GET /ready, and the
ordered migration runner (app/db/migrations.py).
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.routing import Route

from app import mcp_server
from app.config import is_production, settings, validate_settings
from app.db.session import _init as _session_factory
from app.logging_setup import configure_logging
from app.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.services import auth as auth_service
from app.routes import context, contracts, coordination, decisions, environment, github_webhook, invites, projects, reason, tasks, team, users
from app.routes import auth as auth_routes

# ---------------------------------------------------------------------------
# Startup: structured logging first, then central config validation.
# Development keeps booting with loud warnings (a fresh clone can serve
# /health); production refuses to start on a broken configuration.
# ---------------------------------------------------------------------------
configure_logging()
_startup_logger = logging.getLogger("scaffold.startup")
_validation = validate_settings()
for _warning in _validation.warnings:
    _startup_logger.warning("config: %s", _warning)
if not _validation.ok:
    if is_production():
        raise RuntimeError(
            "refusing to start: invalid configuration for SCAFFOLD_ENV=production\n  - "
            + "\n  - ".join(_validation.errors)
        )
    for _error in _validation.errors:
        _startup_logger.warning("config (development, continuing): %s", _error)


class _McpPathFix:
    """mcp_app registers its route at /mcp internally, but a Mount('/mcp') strips
    that prefix (root_path) before the sub-app routes. Rewrite the child path so
    the sub-app always sees '/mcp' after its own root_path stripping."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "") in ("", "/"):
            scope = dict(scope)
            scope["path"] = scope.get("root_path", "") + "/mcp"
        await self.app(scope, receive, send)


class _McpAuth:
    """Bearer gate for the mounted MCP ASGI app.

    Every /mcp request must carry a verifiable credential (Supabase JWT or a
    scaffold_ PAT — same scheme as the HTTP routes). On success the resolved
    Principal is stashed in a ContextVar that tool bodies read
    (auth_service.current_mcp_principal); on failure the request is answered
    401 JSON and never reaches a tool. 503 fail-closed when the engine cannot
    verify credentials (missing SUPABASE_JWT_SECRET).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        raw = headers.get("authorization", "")
        scheme, _, credential = raw.partition(" ")
        credential = credential.strip()

        from starlette.responses import JSONResponse

        async def deny(status: int, detail: str) -> None:
            resp = JSONResponse({"detail": detail}, status_code=status)
            await resp(scope, receive, send)

        if scheme.lower() != "bearer" or not credential:
            await deny(401, "Authorization: Bearer <token> required (Supabase JWT or scaffold_ PAT)")
            return
        if len(credential) > auth_service.MAX_TOKEN_LEN:
            await deny(401, "invalid credential")
            return

        db = None
        try:
            db = _session_factory()()
            try:
                principal = auth_service.resolve_principal(db, _StubRequest(credential))
            finally:
                db.close()
        except auth_service.AuthUnavailable as exc:
            await deny(503, str(exc))
            return
        except auth_service.HTTPException as exc:
            await deny(exc.status_code, str(exc.detail))
            return
        except Exception:  # noqa: BLE001 — malformed/undecodable credentials
            await deny(401, "invalid credential")
            return

        token = auth_service.current_mcp_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            auth_service.current_mcp_principal.reset(token)


class _StubRequest:
    """Just enough of a Starlette request for auth_service._extract_bearer."""

    def __init__(self, credential: str) -> None:
        self.headers = {"authorization": f"Bearer {credential}"}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Apply the ordered migration manifest at BOOT (not on first request), so an
    # orchestrator's readiness probe sees an accurate picture immediately.
    # Fail-open: a missing/unreachable database must not stop /health — /ready
    # reports the degraded state instead.
    if settings.database_url:
        try:
            _session_factory()
        except Exception as exc:  # noqa: BLE001 — fail-open boot
            _startup_logger.warning(
                "database initialisation failed at startup (continuing; GET /ready will report it): %s",
                exc,
            )
    else:
        _startup_logger.warning(
            "DATABASE_URL is not set — skipping migrations (development mode; GET /ready stays 503)"
        )

    # The MCP streamable-HTTP session manager must be running for /mcp to respond.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(title="Scaffold Engine", version="0.3.0", lifespan=lifespan)

# Middleware order (last added = outermost, so the request flows):
#   CORS -> RequestContext -> RateLimit -> routes
# CORS is outermost so even a 429 carries CORS headers; RequestContext wraps
# RateLimit so denied requests are still logged with their request id.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

# Dashboard + plugin call this API from other origins. CORS is configured via
# SCAFFOLD_CORS_ORIGINS (comma-separated); unset/empty keeps the dev wildcard
# (which disables credentialed requests, as browsers require). Production
# validation makes an empty value fatal at startup.
_cors_origins = [o.strip() for o in settings.scaffold_cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)

app.include_router(auth_routes.router)  # Phase 6: /auth/me, PATs, GET/POST /projects
app.include_router(projects.router)
app.include_router(context.router)
app.include_router(tasks.router)
app.include_router(decisions.router)
app.include_router(contracts.router)
app.include_router(github_webhook.router)
app.include_router(reason.router)
app.include_router(invites.router)  # Day 5: teammate invite/join (shareable link)
app.include_router(users.router)  # Phase 2: read-only project roster for the dashboard
app.include_router(team.router)  # Phase 3: members, agent identity, ownership
app.include_router(coordination.router)  # Phase 4: ready tasks, recommendations, next actions
app.include_router(environment.router)  # Phase 5: secure environment (metadata/grants/values)
# MCP: exact Route for POST/DELETE /mcp (avoids Starlette's /mcp -> /mcp/ 307),
# plus the mount for sub-paths. Both go through the Bearer gate (401 without a
# valid credential) and the path-fixing wrapper.
_mcp_wrapped = _McpAuth(_McpPathFix(mcp_server.mcp_app))
app.router.routes.append(Route("/mcp", _mcp_wrapped, methods=["GET", "POST", "DELETE"]))
app.mount("/mcp", _mcp_wrapped)


@app.get("/health")
def health() -> dict:
    """Liveness (frozen shape, unauthenticated): the process is up. Never touches the DB."""
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    """Readiness (unauthenticated — orchestrators cannot carry credentials).

    Checks the database and the migration manifest. 503 until both are good, so
    a rolling deploy never routes traffic to an engine that cannot serve. The
    body carries no configuration values and no error detail (those go to the
    structured log); /health stays the pure liveness probe.
    """
    checks: dict[str, str] = {}
    is_ready = True
    try:
        SessionLocal = _session_factory(apply_migrations=False)
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
            try:
                from app.db.migrations import migration_status

                status = migration_status(db.get_bind())
                if status["pending"]:
                    checks["migrations"] = f"pending:{len(status['pending'])}"
                    is_ready = False
                elif status["checksum_mismatches"]:
                    checks["migrations"] = "checksum_warning"
                else:
                    checks["migrations"] = "ok"
            except Exception as exc:  # noqa: BLE001 — report, never leak
                _startup_logger.warning("/ready: migration check failed: %s", exc)
                checks["migrations"] = "unavailable"
                is_ready = False
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — report, never leak
        _startup_logger.warning("/ready: database check failed: %s", exc)
        checks["database"] = "unavailable"
        is_ready = False

    if not is_ready:
        response.status_code = 503
    return {"status": "ready" if is_ready else "not_ready", "checks": checks, "version": app.version}

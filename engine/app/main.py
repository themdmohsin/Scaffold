"""Scaffold engine — FastAPI backend.

Frozen contracts live in docs/API_CONTRACTS.md and docs/SCHEMA.md.
Day 1: /health, POST /projects, GET /projects/:id/context, tasks CRUD.
Day 2: + github-webhook ingestion, + MCP server mounted at /mcp.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from app import mcp_server
from app.config import settings
from app.db.session import _init as _session_factory
from app.services import auth as auth_service
from app.routes import context, contracts, coordination, decisions, environment, github_webhook, invites, projects, reason, tasks, team, users
from app.routes import auth as auth_routes


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
    # The MCP streamable-HTTP session manager must be running for /mcp to respond.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(title="Scaffold Engine", version="0.2.0", lifespan=lifespan)

# Dashboard + plugin call this API from other origins. CORS is configured via
# SCAFFOLD_CORS_ORIGINS (comma-separated); unset/empty keeps the dev wildcard.
_cors_origins = [o.strip() for o in settings.scaffold_cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {"status": "ok"}

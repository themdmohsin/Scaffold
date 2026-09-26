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
from app.routes import context, contracts, decisions, github_webhook, invites, projects, reason, tasks


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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # The MCP streamable-HTTP session manager must be running for /mcp to respond.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(title="Scaffold Engine", version="0.2.0", lifespan=lifespan)

# Dashboard + plugin call this API from other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon scope; tighten when the dashboard is deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(context.router)
app.include_router(tasks.router)
app.include_router(decisions.router)
app.include_router(contracts.router)
app.include_router(github_webhook.router)
app.include_router(reason.router)
app.include_router(invites.router)  # Day 5: teammate invite/join (shareable link)
# MCP: exact Route for POST/DELETE /mcp (avoids Starlette's /mcp -> /mcp/ 307),
# plus the mount for sub-paths. Both go through the path-fixing wrapper.
_mcp_wrapped = _McpPathFix(mcp_server.mcp_app)
app.router.routes.append(Route("/mcp", _mcp_wrapped, methods=["GET", "POST", "DELETE"]))
app.mount("/mcp", _mcp_wrapped)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

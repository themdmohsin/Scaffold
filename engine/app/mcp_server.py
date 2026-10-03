"""Scaffold MCP server — exposes the 6 frozen tool names (docs/API_CONTRACTS.md).

Mounted into the FastAPI engine at /mcp (streamable HTTP). The OpenCode plugin
(and any MCP client) calls these verbatim:

    get_project_context()
    get_api_contract(route: str)
    get_active_tasks()
    get_recent_decisions()
    report_change(diff_summary: str, files_changed: list[str])
    create_task(title: str, owner_id: str | None, due_at: str | None)

CALLER IDENTITY (Phase 6): every /mcp request must carry Authorization: Bearer
(a Supabase JWT or a scaffold_ PAT — enforced by the ASGI gate in main.py
BEFORE any tool runs). Each tool then requires ACTIVE membership on the
resolved project; identity always comes from the verified credential, never a
tool argument. Legacy optional args (owner_id / user_id) are ACCEPTED but
IGNORED for authorization.

Project resolution: the project_id argument is OPTIONAL on every tool. When omitted,
the server uses SCAFFOLD_DEFAULT_PROJECT_ID (engine .env) — the single-project
convention for the demo. Multi-project clients pass the id explicitly.

All tools hit real Postgres. Deterministic logic stays deterministic (repo rule #3):
no LLM anywhere in this file.
"""

import uuid
from datetime import datetime

from mcp.server.mcpserver import MCPServer

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ApiContract, Decision, EnvironmentVariable, Event, Project, Task, User
from app.db.session import _init
from app.routes.context import build_context
from app.services import auth as auth_service
from app.services import coordination as coord
from app.services import coordination_data as cdata
from app.services import environment as environment_service
from app.services.auth import Principal

mcp = MCPServer("scaffold")


def _db():
    return _init()()


def _resolve_project_id(explicit: str | None) -> uuid.UUID:
    raw = explicit or settings.scaffold_default_project_id
    if not raw:
        raise ValueError(
            "No project specified: pass project_id or set SCAFFOLD_DEFAULT_PROJECT_ID in engine/.env"
        )
    return uuid.UUID(raw)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _principal() -> Principal:
    """The verified caller, stashed by the /mcp Bearer gate (main.py)."""
    principal = auth_service.current_mcp_principal.get()
    if principal is None:
        raise HTTPException(status_code=401, detail="no verified principal — send Authorization: Bearer")
    return principal


def _gate(db: Session, explicit_project_id: str | None) -> tuple[uuid.UUID, Principal]:
    """Shared gate for every tool: resolve the project, require an ACTIVE
    membership for the Bearer principal. 401 unauthenticated, 404 unknown
    project, 403 non-member or PAT scoped to another project."""
    principal = _principal()
    pid = _resolve_project_id(explicit_project_id)
    auth_service.require_member(db, pid, principal)
    return pid, principal


@mcp.tool()
def get_project_context(project_id: str | None = None) -> dict:
    """Always-on project summary: goal, task counts, active tasks, recent decisions, contracts. Attach before coding."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        return build_context(db, pid)
    finally:
        db.close()


@mcp.tool()
def get_api_contract(route: str, project_id: str | None = None) -> dict:
    """Look up a registered API contract by route path (e.g. '/api/auth/login').

    Returns the latest matching contract with method + request/response schemas.
    """
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        row = (
            db.query(ApiContract)
            .filter(ApiContract.project_id == pid, ApiContract.route == route)
            .order_by(ApiContract.created_at.desc())
            .first()
        )
        if not row:
            return {"found": False, "route": route}
        return {
            "found": True,
            "id": str(row.id),
            "route": row.route,
            "method": row.method,
            "request_schema": row.request_schema,
            "response_schema": row.response_schema,
            "created_at": _iso(row.created_at),
        }
    finally:
        db.close()


@mcp.tool()
def get_active_tasks(project_id: str | None = None) -> dict:
    """List non-done tasks (todo + in_progress), oldest first. 'Who is doing what'."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        rows = (
            db.query(Task)
            .filter(Task.project_id == pid, Task.status != "done")
            .order_by(Task.created_at.asc())
            .limit(50)
            .all()
        )
        tasks = [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "owner_id": str(t.owner_id) if t.owner_id else None,
                "due_at": _iso(t.due_at),
            }
            for t in rows
        ]
        # MCP prefers object-shaped returns (bare lists get wrapped by the SDK).
        return {"tasks": tasks, "count": len(tasks)}
    finally:
        db.close()


@mcp.tool()
def get_recent_decisions(limit: int = 5, project_id: str | None = None) -> dict:
    """Most recent decisions (newest first) so agents don't re-litigate them."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        rows = (
            db.query(Decision)
            .filter(Decision.project_id == pid)
            .order_by(Decision.created_at.desc())
            .limit(min(max(limit, 1), 50))
            .all()
        )
        decisions = [
            {
                "id": str(d.id),
                "text": d.text,
                "reasoning": d.reasoning,
                "made_by": str(d.made_by) if d.made_by else None,
                "created_at": _iso(d.created_at),
            }
            for d in rows
        ]
        return {"decisions": decisions, "count": len(decisions)}
    finally:
        db.close()


@mcp.tool()
def report_change(
    diff_summary: str,
    files_changed: list[str],
    project_id: str | None = None,
) -> dict:
    """Report a change made during a coding session (plugin 'after' hook).

    Stores the one-line summary + file paths — never raw source (repo rule #4).
    Writes a change_reported event.
    """
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        event = Event(
            project_id=pid,
            type="change_reported",
            payload={"diff_summary": diff_summary, "files_changed": files_changed},
        )
        db.add(event)
        db.commit()
        return {"ok": True, "event_id": str(event.id)}
    finally:
        db.close()


@mcp.tool()
def create_task(
    title: str,
    owner_id: str | None = None,
    due_at: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Create a task. due_at is ISO-8601. Legacy owner_id is ACCEPTED but
    IGNORED for authorization: the task's owner falls back to the CALLER's
    roster/agent identity when owner_id is omitted or not valid here."""
    db = _db()
    try:
        pid, principal = _gate(db, project_id)
        owner: uuid.UUID | None = None
        if owner_id:
            owner_uuid = uuid.UUID(owner_id)
            user = db.get(User, owner_uuid)
            if user and user.project_id == pid:
                owner = owner_uuid  # legacy hint honored only if valid on this project
        if owner is None:
            owner = auth_service.ensure_roster_user_or_agent(db, pid, principal).id

        task = Task(
            project_id=pid,
            title=title,
            owner_id=owner,
            due_at=datetime.fromisoformat(due_at.replace("Z", "+00:00")) if due_at else None,
        )
        db.add(task)
        db.flush()
        db.add(
            Event(
                project_id=pid,
                type="task_created",
                payload={"task_id": str(task.id), "title": title, "via": "mcp"},
            )
        )
        db.commit()
        return {"id": str(task.id), "title": task.title, "status": task.status}
    finally:
        db.close()


# ---- Phase 4: coordination tools (deterministic; same pure service as the HTTP routes) ----


@mcp.tool()
def get_ready_tasks(project_id: str | None = None) -> dict:
    """Tasks that are ready to start now: todo, not blocked, all dependencies done.
    Explainable — every row carries reasons. Deterministic (no LLM)."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        snap = cdata.load_snapshot(db, pid)
        incomplete = coord.incomplete_deps_per_task(snap["tasks"], snap["dep_edges"])
        downstream = coord.downstream_open_counts(snap["tasks"], snap["dep_edges"])
        ready = [
            t | {"dependency_count": snap["dependency_counts"].get(t["id"], 0)}
            for t in snap["tasks"]
            if coord.is_ready(t, snap["open_blocker_task_ids"], incomplete.get(t["id"], set()))
        ]
        ranked = coord.rank_candidates(
            ready, None, downstream, cdata.utcnow(), incomplete, snap["open_blocker_task_ids"]
        )
        users_by_id = {u["id"]: u for u in snap["users"]}
        tasks_out = [
            {
                "id": t["id"],
                "title": t["title"],
                "priority": t.get("priority") or "medium",
                "owner_id": t.get("owner_id"),
                "owner_name": (users_by_id.get(t.get("owner_id") or "") or {}).get("name"),
                "score": t["score"],
                "reasons": t["reasons"],
            }
            for t in ranked[:10]
        ]
        return {"tasks": tasks_out, "count": len(tasks_out)}
    finally:
        db.close()


@mcp.tool()
def get_recommended_task(user_id: str | None = None, project_id: str | None = None) -> dict:
    """"What should I work on?" — one explainable, deterministic recommendation
    for the CALLER (resolved from the Bearer credential; legacy user_id is
    accepted but ignored). The LLM never picks this; rules over real project
    state do."""
    db = _db()
    try:
        pid, principal = _gate(db, project_id)

        # Identity = the verified caller (roster user for humans, their
        # registered agent for PATs). Legacy user_id is ACCEPTED but IGNORED.
        requester_id: str | None = str(
            auth_service.ensure_roster_user_or_agent(db, pid, principal).id
        )

        snap = cdata.load_snapshot(db, pid)
        rejected = coord.rejected_task_ids_from_events(snap["events"], requester_id or "", cdata.utcnow())

        if requester_id:
            rec = coord.recommend_for_user(
                requester_id,
                snap["tasks"],
                snap["dep_edges"],
                snap["open_blocker_task_ids"],
                {u["id"]: u for u in snap["users"]},
                rejected,
                cdata.utcnow(),
            )
            top = rec.get("recommendation")
            return {
                "recommendation": top,
                "alternates": rec.get("alternates", []),
                "note": rec.get("note"),
            }

        action = coord.project_next_action(
            snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"],
            snap["open_conflicts"], cdata.utcnow(),
        )
        return {"recommendation": action, "alternates": [], "note": None}
    finally:
        db.close()


# ---- Phase 5: secure environment tools (deterministic; no LLM) ----------------
# Metadata + template + AUTHORIZED value retrieval. The value tool resolves
# the CALLER's registered agent (a PAT owner's agent; register via POST
# /projects/:id/agents) and returns values ONLY for variables that agent has
# a grant for — agents get their scoped environment, never the project's
# whole secret set (Phase 5 Feature 10).
# NOTE: the dashboard never calls these; GET /context stays value-free.


@mcp.tool()
def get_project_environment(project_id: str | None = None) -> dict:
    """Project environment STATUS (metadata only — never a secret value):
    which variables exist, required/optional, secret/non-secret, configured.
    Use this to see what configuration a project needs."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        rows = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == pid)
            .order_by(EnvironmentVariable.key.asc())
            .all()
        )
        variables = []
        for v in rows:
            configured = db.execute(
                text(
                    "SELECT 1 FROM scaffold_secrets.environment_secrets "
                    "WHERE environment_variable_id = :id LIMIT 1"
                ),
                {"id": v.id},
            ).first() is not None
            variables.append(
                {
                    "key": v.key,
                    "description": v.description,
                    "required": v.required,
                    "is_secret": v.is_secret,
                    "configured": configured,
                }
            )
        return {"project_id": str(pid), "variables": variables, "count": len(variables)}
    finally:
        db.close()


@mcp.tool()
def get_environment_template(project_id: str | None = None) -> dict:
    """`.env.example` contents for the project: variable names with EMPTY values
    (+ descriptions). Write it to the repo so teammates know what to configure."""
    db = _db()
    try:
        pid, _ = _gate(db, project_id)
        rows = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == pid)
            .order_by(EnvironmentVariable.key.asc())
            .all()
        )
        content = "\n".join(f"{r.key}=" for r in rows)
        descriptions = [{"key": r.key, "description": r.description} for r in rows]
        return {
            "project_id": str(pid),
            "filename": ".env.example",
            "variables": descriptions,
            "content": content,
            "count": len(rows),
        }
    finally:
        db.close()


@mcp.tool()
def request_environment_value(
    key: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """AUTHORIZED retrieval of ONE environment value for the CALLER's runtime.

    Identity comes from the Bearer credential: a PAT caller gets values via
    their REGISTERED AGENT's grants (register via POST /projects/:id/agents);
    legacy user_id is ACCEPTED but IGNORED. Flow: membership check ->
    per-variable grant check -> server-side decrypt -> value. Denied
    (403-shaped error result) without a grant; audited either way. The value
    goes to the CALLING RUNTIME only — never to dashboards, logs, events, or
    other agents.
    """
    db = _db()
    try:
        pid, principal = _gate(db, project_id)
        project = db.get(Project, pid)
        if not project:
            raise ValueError("project not found")
        requester = auth_service.resolve_agent_for_request(db, pid, principal)
        requester_id: uuid.UUID = requester.id

        var = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == pid, EnvironmentVariable.key == key.strip())
            .first()
        )
        if not var:
            return {"found": False, "key": key}

        try:
            result = environment_service.request_secret(db, project, var, requester)
        except HTTPException as exc:
            if exc.status_code == 403:
                return {"authorized": False, "key": key, "detail": exc.detail}
            if exc.status_code == 404:
                return {"found": False, "configured": False, "key": key}
            if exc.status_code == 503:
                return {"error": exc.detail, "key": key}
            raise
        return {"authorized": True, "key": result["key"], "value": result["value"]}
    finally:
        db.close()


# ---- ASGI app for mounting into the engine: /mcp (streamable HTTP) ----
mcp_app = mcp.streamable_http_app()

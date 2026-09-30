"""team.py — Phase 3: deterministic team/membership computation.

Repo rule #3 (AGENTS.md): "who's working on what" / activity status is plain
Python over data already in Postgres — never an LLM call. This module mirrors
the shape of services/availability.py (Day 5): every decision function is
pure (rows in, rows out) and unit-testable without a live database. The route
layer (`app/routes/team.py`) does the DB I/O and calls these functions.

Activity status is explicitly NOT live presence (no websocket/heartbeat exists
yet) — it is derived from real Scaffold events/task rows, bucketed by recency,
per the Phase 3 spec ("don't fake activity; use a clearly defined
activity-based status rather than pretending it is live presence").
"""

from datetime import datetime, timedelta, timezone

ACTIVE_WINDOW = timedelta(minutes=15)
IDLE_WINDOW = timedelta(hours=24)

OPEN_STATUSES = ("todo", "in_progress")


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize to an aware UTC datetime (mirrors availability.py's helper —
    task/event timestamps read back from the DB are already aware, but this
    keeps the function safe if a naive datetime is ever passed in tests)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def compute_last_activity(
    user_id: str,
    tasks: list[dict],
    decisions: list[dict],
    events: list[dict],
) -> datetime | None:
    """Latest timestamp attributable to this user across tasks they own,
    decisions they made, and events whose payload names them.

    tasks:     [{owner_id, created_at}]
    decisions: [{made_by, created_at}]
    events:    [{payload, created_at}]  — payload may carry 'user_id' or
               'owner_id' (task_created/task_updated/teammate_joined/
               agent_registered/member_role_changed all do, see routes/team.py)
    """
    candidates: list[datetime] = []

    for t in tasks:
        if t.get("owner_id") == user_id and t.get("created_at"):
            candidates.append(_as_utc(t["created_at"]))

    for d in decisions:
        if d.get("made_by") == user_id and d.get("created_at"):
            candidates.append(_as_utc(d["created_at"]))

    for e in events:
        payload = e.get("payload") or {}
        if payload.get("user_id") == user_id or payload.get("owner_id") == user_id:
            if e.get("created_at"):
                candidates.append(_as_utc(e["created_at"]))

    return max(candidates) if candidates else None


def compute_current_task(user_id: str, tasks: list[dict]) -> dict | None:
    """Most recently created open (non-done) task owned by this user, or None.

    tasks: [{id, title, status, owner_id, created_at}]
    """
    open_tasks = [
        t for t in tasks if t.get("owner_id") == user_id and t.get("status") != "done"
    ]
    if not open_tasks:
        return None
    open_tasks.sort(key=lambda t: t["created_at"], reverse=True)
    top = open_tasks[0]
    return {"id": top["id"], "title": top["title"], "status": top["status"]}


def is_blocked(user_id: str, tasks: list[dict], open_blocker_task_ids: set) -> bool:
    """True if any open task owned by this user has an unresolved blocker."""
    return any(
        t.get("owner_id") == user_id and t.get("id") in open_blocker_task_ids
        for t in tasks
        if t.get("status") in OPEN_STATUSES
    )


def compute_status(
    now: datetime,
    last_activity_at: datetime | None,
    blocked: bool,
) -> str:
    """ACTIVE | IDLE | BLOCKED | OFFLINE. A blocker takes priority — it's the
    thing a teammate most needs to see. Otherwise: recent activity -> ACTIVE,
    older activity while something is still open -> IDLE, nothing ever -> OFFLINE."""
    if blocked:
        return "BLOCKED"
    if last_activity_at is None:
        return "OFFLINE"
    now = _as_utc(now)
    last_activity_at = _as_utc(last_activity_at)
    delta = now - last_activity_at
    if delta <= ACTIVE_WINDOW:
        return "ACTIVE"
    if delta <= IDLE_WINDOW:
        return "IDLE"
    return "OFFLINE"


def build_member_row(
    user: dict,
    now: datetime,
    tasks: list[dict],
    decisions: list[dict],
    events: list[dict],
    open_blocker_task_ids: set,
) -> dict:
    """Assemble one roster row. user: {id, name, role, kind, agent_provider,
    agent_model, agent_session_id, membership_status, joined_at}."""
    uid = user["id"]
    last_activity = compute_last_activity(uid, tasks, decisions, events)
    blocked = is_blocked(uid, tasks, open_blocker_task_ids)
    return {
        "id": uid,
        "name": user.get("name"),
        "role": user.get("role"),
        "kind": user.get("kind") or "developer",
        "agent_provider": user.get("agent_provider"),
        "agent_model": user.get("agent_model"),
        "membership_status": user.get("membership_status") or "active",
        "joined_at": user.get("joined_at"),
        "current_task": compute_current_task(uid, tasks),
        "activity_status": compute_status(now, last_activity, blocked),
        "last_activity_at": last_activity,
    }

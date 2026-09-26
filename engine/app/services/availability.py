"""availability.py — Day 5 Person A: deterministic "who's free" + assignment validation.

Repo rule #2 / master-doc §9: anything answerable by a database query stays a
database query. Open-task load is a SQL GROUP BY, deadline math is clock math —
the LLM only *chooses among* the facts computed here, and everything it returns
is re-validated by pure functions in this file before it reaches a client.

No LLM and no network in this module. The DB wrapper is thin; every decision
function is pure and unit-testable without Supabase.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Task, User

MAX_ROSTER_ROWS = 10  # keep the /reason context block bounded (repo rule #6)
OPEN_STATUSES = ("todo", "in_progress")  # anything else (done/cancelled) is free


# ---------------------------------------------------------------------------
# Pure core — no DB, no clock reads (tests pass `now` explicitly)
# ---------------------------------------------------------------------------

def compute_roster(users: list[dict], tasks: list[dict]) -> list[dict]:
    """Open-task load per user. Pure: callers pass rows, not ORM objects.

    users: [{id, name, role}]   tasks: [{owner_id, status, due_at}]
    Returns roster rows sorted by load, then name:
    [{user_id, name, role, open_tasks}].
    """
    open_by_owner: dict[str, int] = {}
    for t in tasks:
        if t.get("owner_id") and t.get("status") in OPEN_STATUSES:
            open_by_owner[t["owner_id"]] = open_by_owner.get(t["owner_id"], 0) + 1

    rows = [
        {
            "user_id": u["id"],
            "name": u.get("name") or "unnamed",
            "role": u.get("role") or "",
            "open_tasks": open_by_owner.get(u["id"], 0),
        }
        for u in users
    ]
    rows.sort(key=lambda r: (r["open_tasks"], r["name"].lower()))
    return rows[:MAX_ROSTER_ROWS]


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize to an aware UTC datetime: naive values are assumed UTC.

    Clients POST deadlines without offsets (FastAPI parses them into naive
    datetimes), so every datetime this module compares must pass through here.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hours_until(deadline: datetime | None, now: datetime) -> int | None:
    """Whole hours from `now` until `deadline`; negative once past; None if unset."""
    deadline = _as_utc(deadline)
    if deadline is None:
        return None
    return int((deadline - _as_utc(now)).total_seconds() // 3600)


def render_roster_block(roster_rows: list[dict], deadline: datetime | None, now: datetime) -> str:
    """The bounded TEAM ROSTER block handed to the LLM inside the /reason context."""
    lines: list[str] = ["TEAM ROSTER (assign owner_id ONLY from these user_ids):"]
    if not roster_rows:
        lines.append("(no teammates on this project yet — leave owner_id null)")
    for r in roster_rows:
        role = f", {r['role']}" if r["role"] else ""
        lines.append(f"- {r['name']}{role} — user_id={r['user_id']} — {r['open_tasks']} open task(s)")
    hrs = hours_until(deadline, now)
    if hrs is None:
        lines.append("PROJECT DEADLINE: none set — leave due_at null unless the user names a date.")
    elif hrs < 0:
        lines.append(f"PROJECT DEADLINE: already passed ({abs(hrs)}h ago) — leave due_at null.")
    else:
        lines.append(f"HOURS UNTIL PROJECT DEADLINE: {hrs}")
    return "\n".join(lines)


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return _as_utc(parsed)


def validate_assignments(
    suggested_tasks: list[dict],
    roster_rows: list[dict],
    now: datetime,
    deadline: datetime | None,
) -> tuple[list[dict], list[str]]:
    """Deterministically re-check every LLM-suggested assignment. Pure.

    Rules (fail-open, never throw into /reason):
      - owner_id not on the roster (or not a UUID)  -> null        + note
      - due_at unparseable or in the past           -> null        + note
      - due_at beyond the project deadline          -> clamped to deadline + note
      - no deadline set on the project and a due_at came in -> kept if parseable/future
    Returns (tasks, notes) with the SAME shape as the frozen response:
    [{title, owner_id, due_at}].
    """
    roster_ids = {r["user_id"].strip().lower() for r in roster_rows}
    tasks: list[dict] = []
    notes: list[str] = []
    now = _as_utc(now)
    deadline = _as_utc(deadline)

    for t in suggested_tasks[:3]:
        title = t.get("title", "")
        if not isinstance(title, str) or not title.strip():
            # The frozen response requires a string title; drop junk entries here
            # so this function standalone-holds its never-throw contract.
            notes.append("task without a usable title dropped")
            continue
        title = title.strip()
        owner = t.get("owner_id")
        due = t.get("due_at")

        if owner is not None:
            # LLM-mangled UUIDs are common (case, stray spaces): normalize before
            # comparing, keep the canonical roster spelling when it matches, and
            # clear anything non-string or genuinely unknown.
            candidate = owner.strip().lower() if isinstance(owner, str) else None
            if candidate in roster_ids:
                owner = candidate
            else:
                notes.append(f"'{title[:60]}': owner_id not on roster -> cleared")
                owner = None
        if due is not None:
            parsed = _parse_iso(due) if isinstance(due, str) else None
            if parsed is None:
                notes.append(f"'{title[:60]}': due_at not ISO-8601 -> cleared")
                due = None
            elif parsed < now:
                notes.append(f"'{title[:60]}': due_at in the past -> cleared")
                due = None
            elif deadline is not None and parsed > deadline:
                due = deadline.isoformat()
                notes.append(f"'{title[:60]}': due_at beyond project deadline -> clamped")

        tasks.append({"title": title, "owner_id": owner, "due_at": due})
    return tasks, notes


# ---------------------------------------------------------------------------
# DB wrapper — the only place this module touches the database
# ---------------------------------------------------------------------------

def roster(db: Session, project_id: uuid.UUID) -> list[dict]:
    """Load users + open tasks for the project and compute the roster."""
    users = db.scalars(
        select(User).where(User.project_id == project_id).order_by(User.name.asc())
    ).all()
    counts = dict(
        db.execute(
            select(Task.owner_id, func.count())
            .where(
                Task.project_id == project_id,
                Task.owner_id.isnot(None),
                Task.status.in_(OPEN_STATUSES),
            )
            .group_by(Task.owner_id)
        ).all()
    )
    rows = [
        {
            "user_id": str(u.id),
            "name": u.name,
            "role": u.role or "",
            "open_tasks": int(counts.get(u.id, 0)),
        }
        for u in users
    ]
    rows.sort(key=lambda r: (r["open_tasks"], r["name"].lower()))
    return rows[:MAX_ROSTER_ROWS]

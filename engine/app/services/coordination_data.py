"""coordination_data.py — Phase 4: plain-row loaders for the coordination service.

Thin DB I/O only: every query here loads plain dicts/rows and stringifies UUIDs
so the pure functions in services/coordination.py (and the tests) can run
without ORM objects. Mirrors routes/team.py's `_load_roster_rows` approach
(repo rule #6: bounded, targeted reads — never a full project dump).

No LLM anywhere in this file (rule #1); no business logic either (rule #3) —
the intelligence lives in services/coordination.py's pure functions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiContract, Blocker, Event, Task, TaskDependency, User


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _owner_id_str(value) -> str | None:
    return str(value) if value else None


def load_tasks(db: Session, project_id: uuid.UUID) -> list[dict]:
    """All tasks for the project as plain rows (newest last, matching the
    frozen GET /tasks ordering)."""
    rows = db.scalars(
        select(Task).where(Task.project_id == project_id).order_by(Task.created_at.asc())
    ).all()
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "status": t.status,
            "owner_id": _owner_id_str(t.owner_id),
            "due_at": _iso(t.due_at),
            "created_at": _iso(t.created_at),
            "description": t.description,
            "priority": t.priority or "medium",
            "blocked": bool(t.blocked),
        }
        for t in rows
    ]


def load_dep_edges(db: Session, project_id: uuid.UUID) -> list[tuple[str, str]]:
    """(task_id, depends_on_task_id) string tuples."""
    rows = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.task_id.in_(select(Task.id).where(Task.project_id == project_id))
        )
    ).all()
    return [(str(a), str(b)) for a, b in rows]


def load_open_blocker_task_ids(db: Session, project_id: uuid.UUID) -> set[str]:
    """Task ids with an unresolved blockers row (manual blocks + nothing else —
    contract conflicts have task_id NULL and are NOT per-task blockers)."""
    rows = db.scalars(
        select(Blocker.task_id).where(
            Blocker.project_id == project_id,
            Blocker.resolved.is_(False),
            Blocker.task_id.is_not(None),
        )
    ).all()
    return {str(r) for r in rows}


def load_dependency_counts(db: Session, project_id: uuid.UUID) -> dict[str, int]:
    """task_id -> total number of declared dependencies (ready_reasons uses it)."""
    rows = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.task_id.in_(select(Task.id).where(Task.project_id == project_id))
        )
    ).all()
    counts: dict[str, int] = {}
    for a, _b in rows:
        counts[str(a)] = counts.get(str(a), 0) + 1
    return counts


def load_users(db: Session, project_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(
        select(User).where(User.project_id == project_id).order_by(User.name.asc())
    ).all()
    return [
        {
            "id": str(u.id),
            "name": u.name,
            "kind": u.kind or "developer",
            "membership_status": u.membership_status or "active",
        }
        for u in rows
    ]


def load_contracts(db: Session, project_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(
        select(ApiContract).where(ApiContract.project_id == project_id)
    ).all()
    return [
        {
            "id": str(c.id),
            "route": c.route,
            "method": c.method,
            "created_by_task_id": _owner_id_str(c.created_by_task_id),
        }
        for c in rows
    ]


def load_events(db: Session, project_id: uuid.UUID, limit: int = 500) -> list[dict]:
    """Newest events first, bounded (repo rule #6)."""
    rows = db.scalars(
        select(Event)
        .where(Event.project_id == project_id)
        .order_by(Event.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {"type": e.type, "payload": e.payload or {}, "created_at": _iso(e.created_at)}
        for e in rows
    ]


def load_open_conflicts(db: Session, project_id: uuid.UUID, limit: int = 10) -> list[dict]:
    """Unresolved blockers with task_id NULL — the contract-shape conflicts the
    Day 4 detector records. Exactly what GET /context calls open_conflicts."""
    rows = db.scalars(
        select(Blocker)
        .where(
            Blocker.project_id == project_id,
            Blocker.resolved.is_(False),
            Blocker.task_id.is_(None),
        )
        .order_by(Blocker.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {"id": str(b.id), "description": b.description, "created_at": _iso(b.created_at)}
        for b in rows
    ]


def load_open_task_blockers(db: Session, project_id: uuid.UUID, limit: int = 25) -> list[dict]:
    """Unresolved blockers attached to a task (manual block switches)."""
    rows = db.scalars(
        select(Blocker)
        .where(
            Blocker.project_id == project_id,
            Blocker.resolved.is_(False),
            Blocker.task_id.is_not(None),
        )
        .order_by(Blocker.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(b.id),
            "description": b.description,
            "task_id": str(b.task_id),
            "created_at": _iso(b.created_at),
        }
        for b in rows
    ]


def load_snapshot(db: Session, project_id: uuid.UUID) -> dict:
    """One bounded read of everything the coordination endpoints need."""
    return {
        "tasks": load_tasks(db, project_id),
        "dep_edges": load_dep_edges(db, project_id),
        "open_blocker_task_ids": load_open_blocker_task_ids(db, project_id),
        "dependency_counts": load_dependency_counts(db, project_id),
        "users": load_users(db, project_id),
        "contracts": load_contracts(db, project_id),
        "events": load_events(db, project_id),
        "open_conflicts": load_open_conflicts(db, project_id),
        "open_task_blockers": load_open_task_blockers(db, project_id),
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

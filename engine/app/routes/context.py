"""Context route: GET /projects/:id/context — the always-on summary (master doc §7).

Shape frozen in docs/API_CONTRACTS.md; counts and active tasks are real from Day 1,
decisions/contracts sections fill in on Day 3 when retrieval exists. Day 5 adds two
ADDITIVE keys (blockers, recent_events) so open conflicts are visible through the
same endpoint the dashboard and MCP clients already poll.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Blocker, Event, Project, Task
from app.db.session import get_db
from app.services import retrieval

router = APIRouter(tags=["context"])

CONTEXT_CONTRACT = {
    "project": {"id": "...", "name": "...", "goal": None, "deadline": None},
    "tasks": {"todo": 0, "in_progress": 0, "done": 0},
    "active_tasks": [],
    "recent_decisions": [],
    "relevant_contracts": [],
    "blockers": [],
    "recent_events": [],
    "generated_at": "ISO-8601",
}


def build_context(db: Session, project_id: uuid.UUID) -> dict:
    """Assemble the always-on summary. Shared by the HTTP route and the MCP server."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    counts = dict(
        db.execute(
            select(Task.status, func.count())
            .where(Task.project_id == project_id)
            .group_by(Task.status)
        ).all()
    )

    active = db.scalars(
        select(Task)
        .where(Task.project_id == project_id, Task.status != "done")
        .order_by(Task.created_at.asc())
        .limit(10)
    ).all()

    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "goal": project.goal,
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "created_at": project.created_at.isoformat(),
        },
        "tasks": {
            "todo": counts.get("todo", 0),
            "in_progress": counts.get("in_progress", 0),
            "done": counts.get("done", 0),
        },
        "active_tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "owner_id": str(t.owner_id) if t.owner_id else None,
                "due_at": t.due_at.isoformat() if t.due_at else None,
            }
            for t in active
        ],
        "recent_decisions": [
            {"id": d["id"], "text": d["text"], "created_at": d["created_at"]}
            for d in retrieval.recent_decisions(db, project_id, 5)
        ],
        "relevant_contracts": [
            {"route": c["route"], "method": c["method"]}
            for c in retrieval.recent_contracts(db, project_id, 5)
        ],
        # Day 5 (additive): open blockers + the newest events. This is how the
        # conflict moment (event + blocker) becomes visible to the dashboard
        # and to MCP clients polling the always-on summary — repo rule #6:
        # bounded lists, never an unbounded dump.
        "blockers": [
            {
                "id": str(b.id),
                "description": b.description,
                "resolved": b.resolved,
                "created_at": b.created_at.isoformat(),
            }
            for b in db.scalars(
                select(Blocker)
                .where(Blocker.project_id == project_id, Blocker.resolved.is_(False))
                .order_by(Blocker.created_at.desc())
                .limit(10)
            ).all()
        ],
        "recent_events": [
            {
                "id": str(e.id),
                "type": e.type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in db.scalars(
                select(Event)
                .where(Event.project_id == project_id)
                .order_by(Event.created_at.desc())
                .limit(8)
            ).all()
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/projects/{project_id}/context")
def get_context(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return build_context(db, project_id)

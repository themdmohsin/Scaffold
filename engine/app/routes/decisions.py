"""Decisions routes: GET + POST /projects/:id/decisions (frozen shapes)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Decision, Event, Project, User
from app.db.session import get_db
from app.services import retrieval

router = APIRouter(prefix="/projects/{project_id}/decisions", tags=["decisions"])


class DecisionCreate(BaseModel):
    text: str = Field(min_length=1)
    reasoning: str | None = None
    made_by: uuid.UUID | None = None


def _out(d: Decision) -> dict:
    return {
        "id": str(d.id),
        "project_id": str(d.project_id) if d.project_id else None,
        "text": d.text,
        "reasoning": d.reasoning,
        "made_by": str(d.made_by) if d.made_by else None,
        "created_at": d.created_at.isoformat(),
    }


@router.get("")
def list_decisions(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    rows = db.scalars(
        select(Decision)
        .where(Decision.project_id == project_id)
        .order_by(Decision.created_at.desc())
    ).all()
    return [_out(d) for d in rows]


@router.post("", status_code=201)
def create_decision(
    project_id: uuid.UUID, body: DecisionCreate, db: Session = Depends(get_db)
) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    if body.made_by:
        user = db.get(User, body.made_by)
        if not user or user.project_id != project_id:
            raise HTTPException(status_code=400, detail="made_by is not a user on this project")
    d = Decision(
        project_id=project_id,
        text=body.text,
        reasoning=body.reasoning,
        made_by=body.made_by,
    )
    retrieval.embed_decision_row(d)  # Day 3 pgvector; fail open -> embedding null
    db.add(d)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            type="decision_logged",
            payload={"decision_id": str(d.id), "text": d.text[:200]},
        )
    )
    db.commit()
    db.refresh(d)
    return _out(d)

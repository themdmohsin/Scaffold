"""Users route — Phase 2 addition (Project Control Center).

Read-only, project-scoped: the dashboard needs a roster to populate the
assignee dropdown and to label "Active work" cards by name instead of a raw
UUID. No schema changes (the `users` table is frozen Day 1); this is the
first GET endpoint for it.

GET /projects/:id/users -> 200 [ { id, project_id, name, role } ] (by name asc)
                            404 unknown project
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project, User
from app.db.session import get_db
from app.schemas import UserOut

router = APIRouter(prefix="/projects/{project_id}/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[User]:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    return db.scalars(
        select(User).where(User.project_id == project_id).order_by(User.name.asc())
    ).all()

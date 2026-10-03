"""Projects routes — Phase 6 (real authentication).

POST /projects moved to routes/auth.py (requirement 6: the created project is
owned by the verified CALLER — response shape unchanged plus the additive
owner_user_id). This module keeps the frozen read route:

    GET /projects/{project_id} → 200 ProjectOut | 404

Authorization: any ACTIVE member of the project (owner/admin/member alike);
everyone else gets 403, unknown projects 404 (existence not leaked to
non-members). Identity comes from the verified principal — never a body or
query field.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.schemas import ProjectOut
from app.services.auth import Principal, require_member, require_principal

router = APIRouter(tags=["projects"])


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> Project:
    require_member(db, project_id, principal)
    return db.get(Project, project_id)
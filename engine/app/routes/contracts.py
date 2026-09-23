"""API contracts routes: GET + POST /projects/:id/contracts (frozen shapes).

POST is what the plugin's "after" hook calls to register a new contract
(master doc §4 step 7) and what the webhook uses implicitly via diff parsing.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiContract, Event, Project, Task
from app.db.session import get_db

router = APIRouter(prefix="/projects/{project_id}/contracts", tags=["contracts"])


class ContractCreate(BaseModel):
    route: str = Field(min_length=1)
    method: str = Field(min_length=1, pattern="^[A-Z]+$")
    request_schema: dict | None = None
    response_schema: dict | None = None
    created_by_task_id: uuid.UUID | None = None


def _out(c: ApiContract) -> dict:
    return {
        "id": str(c.id),
        "project_id": str(c.project_id) if c.project_id else None,
        "route": c.route,
        "method": c.method,
        "request_schema": c.request_schema,
        "response_schema": c.response_schema,
        "created_by_task_id": str(c.created_by_task_id) if c.created_by_task_id else None,
        "created_at": c.created_at.isoformat(),
    }


@router.get("")
def list_contracts(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    rows = db.scalars(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.created_at.desc())
    ).all()
    return [_out(c) for c in rows]


@router.post("", status_code=201)
def create_contract(
    project_id: uuid.UUID, body: ContractCreate, db: Session = Depends(get_db)
) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    if body.created_by_task_id:
        task = db.get(Task, body.created_by_task_id)
        if not task or task.project_id != project_id:
            raise HTTPException(status_code=400, detail="created_by_task_id is not a task on this project")
    c = ApiContract(
        project_id=project_id,
        route=body.route,
        method=body.method.upper(),
        request_schema=body.request_schema,
        response_schema=body.response_schema,
        created_by_task_id=body.created_by_task_id,
    )
    db.add(c)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            type="contract_registered",
            payload={"contract_id": str(c.id), "route": c.route, "method": c.method},
        )
    )
    db.commit()
    db.refresh(c)
    return _out(c)

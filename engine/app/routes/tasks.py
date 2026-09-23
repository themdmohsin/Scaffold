"""Tasks routes: the four frozen task endpoints, with events written on mutation."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Task, User
from app.db.session import get_db
from app.schemas import TaskCreate, TaskOut, TaskUpdate

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


def _task_out(task: Task) -> dict:
    return {
        "id": str(task.id),
        "project_id": str(task.project_id) if task.project_id else None,
        "title": task.title,
        "status": task.status,
        "owner_id": str(task.owner_id) if task.owner_id else None,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "created_at": task.created_at.isoformat(),
    }


def _require_project(db: Session, project_id: uuid.UUID) -> None:
    from app.db.models import Project

    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")


def _validate_owner(db: Session, project_id: uuid.UUID, owner_id: uuid.UUID | None) -> None:
    if owner_id is None:
        return
    user = db.get(User, owner_id)
    if not user or user.project_id != project_id:
        raise HTTPException(status_code=400, detail="owner_id is not a user on this project")


@router.get("")
def list_tasks(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    _require_project(db, project_id)
    tasks = db.scalars(
        select(Task).where(Task.project_id == project_id).order_by(Task.created_at.asc())
    ).all()
    return [_task_out(t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(
    project_id: uuid.UUID, body: TaskCreate, db: Session = Depends(get_db)
) -> Task:
    _require_project(db, project_id)
    _validate_owner(db, project_id, body.owner_id)
    task = Task(
        project_id=project_id,
        title=body.title,
        owner_id=body.owner_id,
        due_at=body.due_at,
    )
    db.add(task)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            type="task_created",
            payload={"task_id": str(task.id), "title": task.title},
        )
    )
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdate,
    db: Session = Depends(get_db),
) -> Task:
    _require_project(db, project_id)
    task = db.scalar(
        select(Task).where(Task.id == task_id, Task.project_id == project_id)
    )
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    changed = {}
    if body.status is not None and body.status != task.status:
        task.status = body.status
        changed["status"] = body.status
    if body.owner_id is not None and body.owner_id != task.owner_id:
        _validate_owner(db, project_id, body.owner_id)
        task.owner_id = body.owner_id
        changed["owner_id"] = str(body.owner_id)

    if changed:
        db.add(
            Event(
                project_id=project_id,
                type="task_updated",
                payload={"task_id": str(task.id), **changed},
            )
        )
        db.commit()
        db.refresh(task)
    return task

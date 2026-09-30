"""Tasks routes: the frozen task CRUD endpoints, plus Phase 2 (Project Control
Center) additions — description/priority/blocked/dependencies/full editing.

The four Day 1 endpoints keep their frozen shapes byte-identical (title,
status, owner_id, due_at, created_at, id, project_id) and keep writing the
frozen `task_created` / `task_updated` events exactly as before. Every new
field is additive on the response, and every new event type is an EXTRA
event next to the frozen one — nothing that already reads these routes or
events breaks.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Blocker, Event, Task, TaskDependency, User
from app.db.session import get_db
from app.schemas import DependencyCreate, TaskCreate, TaskOut, TaskUpdate

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


def _dependency_rows(db: Session, task_id: uuid.UUID) -> tuple[list[dict], list[dict]]:
    """Returns (dependencies, blocked_by_dependencies) — both lists of
    {id, title, status}. blocked_by_dependencies is the subset that is not
    'done' yet, i.e. what is keeping this task blocked by dependency."""
    rows = db.execute(
        select(Task.id, Task.title, Task.status)
        .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
        .where(TaskDependency.task_id == task_id)
        .order_by(Task.created_at.asc())
    ).all()
    deps = [{"id": str(r.id), "title": r.title, "status": r.status} for r in rows]
    blocking = [d for d in deps if d["status"] != "done"]
    return deps, blocking


def _task_out(db: Session, task: Task) -> dict:
    deps, blocking = _dependency_rows(db, task.id)
    return {
        "id": str(task.id),
        "project_id": str(task.project_id) if task.project_id else None,
        "title": task.title,
        "status": task.status,
        "owner_id": str(task.owner_id) if task.owner_id else None,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "created_at": task.created_at.isoformat(),
        "description": task.description,
        "priority": task.priority or "medium",
        "blocked": bool(task.blocked),
        "created_by": str(task.created_by) if task.created_by else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "dependencies": deps,
        "blocked_by_dependencies": blocking,
        # A task reads as blocked in the UI if it was manually marked blocked,
        # OR if it depends on something that is not done yet.
        "is_blocked": bool(task.blocked) or len(blocking) > 0,
    }


def _require_project(db: Session, project_id: uuid.UUID) -> None:
    from app.db.models import Project

    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")


def _require_task(db: Session, project_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def _validate_owner(db: Session, project_id: uuid.UUID, owner_id: uuid.UUID | None) -> None:
    if owner_id is None:
        return
    user = db.get(User, owner_id)
    if not user or user.project_id != project_id:
        raise HTTPException(status_code=400, detail="owner_id is not a user on this project")


def _validate_dependency_target(
    db: Session, project_id: uuid.UUID, task_id: uuid.UUID, depends_on_task_id: uuid.UUID
) -> None:
    if depends_on_task_id == task_id:
        raise HTTPException(status_code=400, detail="a task cannot depend on itself")
    target = db.scalar(select(Task).where(Task.id == depends_on_task_id, Task.project_id == project_id))
    if not target:
        raise HTTPException(status_code=400, detail="depends_on_task_id is not a task on this project")


def _open_task_blocker_description(task: Task, reason: str | None) -> str:
    base = f"Task blocked: {task.title}"
    return f"{base} — {reason.strip()}" if reason and reason.strip() else base


def _resolve_task_blockers(db: Session, project_id: uuid.UUID, task_id: uuid.UUID) -> None:
    open_blockers = db.scalars(
        select(Blocker).where(
            Blocker.project_id == project_id,
            Blocker.task_id == task_id,
            Blocker.resolved.is_(False),
        )
    ).all()
    for b in open_blockers:
        b.resolved = True


@router.get("", response_model=list[TaskOut])
def list_tasks(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    _require_project(db, project_id)
    tasks = db.scalars(
        select(Task).where(Task.project_id == project_id).order_by(Task.created_at.asc())
    ).all()
    return [_task_out(db, t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(project_id: uuid.UUID, body: TaskCreate, db: Session = Depends(get_db)) -> dict:
    _require_project(db, project_id)
    _validate_owner(db, project_id, body.owner_id)
    _validate_owner(db, project_id, body.created_by)  # created_by is also a project user

    task = Task(
        project_id=project_id,
        title=body.title,
        owner_id=body.owner_id,
        due_at=body.due_at,
        description=body.description,
        priority=body.priority,
        created_by=body.created_by,
    )
    db.add(task)
    db.flush()

    for dep_id in dict.fromkeys(body.dependencies):  # de-dupe, keep order
        _validate_dependency_target(db, project_id, task.id, dep_id)
        db.add(TaskDependency(task_id=task.id, depends_on_task_id=dep_id))

    # Frozen event — payload shape unchanged (task_id, title).
    db.add(Event(project_id=project_id, type="task_created", payload={"task_id": str(task.id), "title": task.title}))
    if body.owner_id is not None:
        db.add(
            Event(
                project_id=project_id,
                type="task_assigned",
                payload={"task_id": str(task.id), "title": task.title, "owner_id": str(body.owner_id)},
            )
        )

    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdate,
    db: Session = Depends(get_db),
) -> dict:
    _require_project(db, project_id)
    task = _require_task(db, project_id, task_id)

    changed: dict = {}
    extra_events: list[Event] = []

    if body.status is not None and body.status != task.status:
        old_status = task.status
        task.status = body.status
        changed["status"] = body.status
        if body.status == "done" and task.completed_at is None:
            from datetime import datetime, timezone

            task.completed_at = datetime.now(timezone.utc)
        elif body.status != "done":
            task.completed_at = None
        extra_events.append(
            Event(
                project_id=project_id,
                type="task_status_changed",
                payload={"task_id": str(task.id), "title": task.title, "from": old_status, "to": body.status},
            )
        )
        if body.status == "done":
            extra_events.append(
                Event(
                    project_id=project_id,
                    type="task_completed",
                    payload={"task_id": str(task.id), "title": task.title},
                )
            )

    if body.owner_id is not None and body.owner_id != task.owner_id:
        _validate_owner(db, project_id, body.owner_id)
        task.owner_id = body.owner_id
        changed["owner_id"] = str(body.owner_id)
        extra_events.append(
            Event(
                project_id=project_id,
                type="task_assigned",
                payload={"task_id": str(task.id), "title": task.title, "owner_id": str(body.owner_id)},
            )
        )

    if body.title is not None and body.title != task.title:
        task.title = body.title
        changed["title"] = body.title

    if body.description is not None and body.description != task.description:
        task.description = body.description
        changed["description"] = body.description

    if body.priority is not None and body.priority != task.priority:
        old_priority = task.priority
        task.priority = body.priority
        changed["priority"] = body.priority
        extra_events.append(
            Event(
                project_id=project_id,
                type="task_priority_changed",
                payload={"task_id": str(task.id), "title": task.title, "from": old_priority, "to": body.priority},
            )
        )

    if body.blocked is not None and body.blocked != task.blocked:
        task.blocked = body.blocked
        changed["blocked"] = body.blocked
        if body.blocked:
            description = _open_task_blocker_description(task, body.blocker_reason)
            existing = db.scalars(
                select(Blocker).where(
                    Blocker.project_id == project_id,
                    Blocker.task_id == task.id,
                    Blocker.resolved.is_(False),
                )
            ).first()
            if not existing:
                db.add(Blocker(project_id=project_id, task_id=task.id, description=description, resolved=False))
            extra_events.append(
                Event(
                    project_id=project_id,
                    type="task_blocked",
                    payload={"task_id": str(task.id), "title": task.title, "reason": body.blocker_reason},
                )
            )
        else:
            _resolve_task_blockers(db, project_id, task.id)
            extra_events.append(
                Event(
                    project_id=project_id,
                    type="task_unblocked",
                    payload={"task_id": str(task.id), "title": task.title},
                )
            )

    if changed:
        # Frozen event — payload shape unchanged in spirit (task_id + changed
        # fields); values are now real for the additive fields too.
        db.add(Event(project_id=project_id, type="task_updated", payload={"task_id": str(task.id), **changed}))
        for ev in extra_events:
            db.add(ev)
        db.commit()
        db.refresh(task)
    return _task_out(db, task)


@router.post("/{task_id}/dependencies", response_model=TaskOut, status_code=201)
def add_dependency(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: DependencyCreate,
    db: Session = Depends(get_db),
) -> dict:
    """Add a `task depends on depends_on_task_id` edge (task_dependencies row)."""
    _require_project(db, project_id)
    task = _require_task(db, project_id, task_id)
    _validate_dependency_target(db, project_id, task_id, body.depends_on_task_id)

    existing = db.get(TaskDependency, {"task_id": task_id, "depends_on_task_id": body.depends_on_task_id})
    if not existing:
        db.add(TaskDependency(task_id=task_id, depends_on_task_id=body.depends_on_task_id))
        db.add(
            Event(
                project_id=project_id,
                type="task_dependency_added",
                payload={"task_id": str(task_id), "depends_on_task_id": str(body.depends_on_task_id)},
            )
        )
        db.commit()
    return _task_out(db, task)


@router.delete("/{task_id}/dependencies/{depends_on_task_id}", response_model=TaskOut, status_code=200)
def remove_dependency(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    depends_on_task_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    _require_project(db, project_id)
    task = _require_task(db, project_id, task_id)

    existing = db.get(TaskDependency, {"task_id": task_id, "depends_on_task_id": depends_on_task_id})
    if existing:
        db.delete(existing)
        db.add(
            Event(
                project_id=project_id,
                type="task_dependency_removed",
                payload={"task_id": str(task_id), "depends_on_task_id": str(depends_on_task_id)},
            )
        )
        db.commit()
    return _task_out(db, task)

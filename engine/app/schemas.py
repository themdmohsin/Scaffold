"""Shared API schemas — shapes frozen in docs/API_CONTRACTS.md."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str
    goal: str | None = None
    deadline: datetime | None = None
    # Phase 6.5 (dashboard team app) additive: optional GitHub repo link, stored
    # as "owner/repo" — display/metadata only, no webhook registration here.
    github_repo: str | None = Field(default=None, max_length=200)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal: str | None
    deadline: datetime | None
    created_at: datetime
    # Phase 6.5 additive — absent from rows created before the migration.
    github_repo: str | None = None


PRIORITY_PATTERN = "^(low|medium|high|urgent)$"
STATUS_PATTERN = "^(todo|in_progress|review|done)$"  # 'review' added Phase 2


class TaskCreate(BaseModel):
    title: str = Field(min_length=1)
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None
    # Phase 2 additions — all optional, all additive to the frozen shape.
    description: str | None = None
    priority: str = Field(default="medium", pattern=PRIORITY_PATTERN)
    created_by: uuid.UUID | None = None
    # Convenience: create the task's dependency edges in the same call
    # (task_dependencies rows) instead of a follow-up request per edge.
    dependencies: list[uuid.UUID] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    status: str | None = Field(default=None, pattern=STATUS_PATTERN)
    owner_id: uuid.UUID | None = None
    # Phase 2 additions — full task editing, priority, and the manual block switch.
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority: str | None = Field(default=None, pattern=PRIORITY_PATTERN)
    blocked: bool | None = None
    # Only read when `blocked` is being set to true: becomes the paired
    # blockers row's description so the Blockers panel shows a reason.
    blocker_reason: str | None = None
    # Phase 6.5 (dashboard team app) additive — the frozen `due_at` column was
    # already on tasks Day 1, but PATCH never accepted it. Follows the same
    # Optional-without-clear semantics as `owner_id` (set/change supported).
    due_at: datetime | None = None


class DependencyCreate(BaseModel):
    depends_on_task_id: uuid.UUID


class TaskDependencyOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    status: str
    owner_id: uuid.UUID | None
    due_at: datetime | None
    created_at: datetime
    # Phase 2 additions.
    description: str | None = None
    priority: str = "medium"
    blocked: bool = False
    created_by: uuid.UUID | None = None
    completed_at: datetime | None = None
    # Computed, not columns: the tasks this one depends on, and — of those —
    # the ones still incomplete ("Blocked by: <title>" in the UI).
    dependencies: list[TaskDependencyOut] = Field(default_factory=list)
    blocked_by_dependencies: list[TaskDependencyOut] = Field(default_factory=list)
    is_blocked: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    role: str | None

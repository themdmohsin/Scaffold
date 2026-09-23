"""Shared API schemas — shapes frozen in docs/API_CONTRACTS.md."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str
    goal: str | None = None
    deadline: datetime | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal: str | None
    deadline: datetime | None
    created_at: datetime


class TaskCreate(BaseModel):
    title: str = Field(min_length=1)
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None


class TaskUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(todo|in_progress|done)$")
    owner_id: uuid.UUID | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    status: str
    owner_id: uuid.UUID | None
    due_at: datetime | None
    created_at: datetime

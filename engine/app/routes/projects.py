"""Projects routes: POST /projects (frozen Day 1 as a required addition)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.schemas import ProjectCreate, ProjectOut

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    existing = db.scalar(select(Project).where(Project.name == body.name))
    if existing:
        raise HTTPException(status_code=409, detail="project with this name already exists")
    project = Project(name=body.name, goal=body.goal, deadline=body.deadline)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project
@router.get("/api/test/scaffold-v2")
def scaffold_test():
    return {"message": "Scaffold API contract test"}
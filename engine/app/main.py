"""Scaffold engine — FastAPI backend.

Frozen contracts live in docs/API_CONTRACTS.md and docs/SCHEMA.md.
Day 1 scope: /health, POST /projects, GET /projects/:id/context, tasks CRUD.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import context, projects, tasks

app = FastAPI(title="Scaffold Engine", version="0.1.0")

# Dashboard + plugin call this API from other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon scope; tighten when the dashboard is deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(context.router)
app.include_router(tasks.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

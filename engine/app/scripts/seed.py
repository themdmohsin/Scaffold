"""Seed script: creates a demo project + one user so every route is testable.

Usage (from engine/):
    python -m app.scripts.seed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.models import Project, User  # noqa: E402
from app.db.session import _init  # noqa: E402


def main() -> None:
    SessionLocal = _init()
    db = SessionLocal()
    try:
        project = Project(
            name="Scaffold Demo",
            goal="Ship the Day 1 foundation and freeze the contracts",
        )
        db.add(project)
        db.flush()
        user = User(project_id=project.id, name="Mohammed", role="builder")
        db.add(user)
        db.commit()
        print(f"project_id={project.id}")
        print(f"user_id={user.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

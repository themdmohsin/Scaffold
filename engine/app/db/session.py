"""Database session management for the Scaffold engine.

The engine is created lazily so the app can boot (and serve /health) before
DATABASE_URL exists — e.g. right after cloning, before Supabase is set up.
"""

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _init() -> sessionmaker:
    global _engine, _SessionLocal
    if _SessionLocal is not None:
        return _SessionLocal

    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set — copy engine/.env.example to engine/.env "
            "and paste the Supabase pooler connection string."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    _engine = create_engine(url, pool_pre_ping=True, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    SessionLocal = _init()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

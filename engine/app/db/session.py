"""Database session management for the Scaffold engine.

The engine is created lazily so the app can boot (and serve /health) before
DATABASE_URL exists — e.g. right after cloning, before Supabase is set up.
"""

from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_migration_applied = False


def _apply_day3_migration(engine: Engine) -> None:
    """Idempotent Day 3 DDL (migrate_day3.sql): pgvector + embedding columns +
    realtime publication. Runs once per process; failure is non-fatal (the engine
    still boots and retrieval falls back to keyword search) — e.g. when the DB
    role lacks CREATE EXTENSION privilege. Can also be run by hand in the SQL editor.
    """
    global _migration_applied
    if _migration_applied:
        return
    _migration_applied = True
    sql = Path(__file__).with_name("migrate_day3.sql").read_text(encoding="utf-8")
    try:
        with engine.connect() as conn:
            # No bound params -> psycopg 3 simple query protocol -> multi-statement OK.
            conn.exec_driver_sql(sql)
            conn.commit()
        print("[scaffold] day3 migration applied (pgvector + realtime publication)")
    except Exception as exc:  # noqa: BLE001 — never block boot on migration trouble
        print(f"[scaffold] day3 migration warning (continuing): {exc}")


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
    # We depend on psycopg 3, so the URL must use the postgresql+psycopg:// scheme.
    # Normalize the common Supabase forms (postgres://, postgresql://) onto it.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    _engine = create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args={"connect_timeout": 10},
    )
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    _apply_day3_migration(_engine)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    SessionLocal = _init()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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


def _apply_sql_migration(engine: Engine, filename: str, label: str) -> None:
    """Run one idempotent .sql migration file. Failure is non-fatal (the engine
    still boots) — e.g. when the DB role lacks a required privilege. Can also be
    run by hand in the Supabase SQL editor."""
    sql = Path(__file__).with_name(filename).read_text(encoding="utf-8")
    try:
        with engine.connect() as conn:
            # No bound params -> psycopg 3 simple query protocol -> multi-statement OK.
            conn.exec_driver_sql(sql)
            conn.commit()
        print(f"[scaffold] {label} migration applied")
    except Exception as exc:  # noqa: BLE001 — never block boot on migration trouble
        print(f"[scaffold] {label} migration warning (continuing): {exc}")


def _apply_day3_migration(engine: Engine) -> None:
    """Idempotent Day 3 DDL (migrate_day3.sql): pgvector + embedding columns +
    realtime publication."""
    _apply_sql_migration(engine, "migrate_day3.sql", "day3 (pgvector + realtime)")


def _apply_phase2_migration(engine: Engine) -> None:
    """Idempotent Phase 2 DDL (migrate_phase2.sql): task board columns
    (description/priority/blocked/created_by/completed_at), the widened
    'review' status, and blockers/task_dependencies joining realtime."""
    _apply_sql_migration(engine, "migrate_phase2.sql", "phase2 (task board)")


def _apply_phase3_migration(engine: Engine) -> None:
    """Idempotent Phase 3 DDL (migrate_phase3.sql): agent identity columns on
    `users`, `projects.owner_user_id`, membership status."""
    _apply_sql_migration(engine, "migrate_phase3.sql", "phase3 (team collaboration)")


def _apply_phase5_migration(engine: Engine) -> None:
    """Idempotent Phase 5 DDL (migrate_phase5.sql): the secure-environment
    metadata/permission tables (public.environment_variables, public.environment_access),
    the secret-value layer (scaffold_secrets schema: keyring + ciphertext), and
    realtime publication for the metadata tables."""
    _apply_sql_migration(engine, "migrate_phase5.sql", "phase5 (secure environment)")


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
    _apply_phase2_migration(_engine)
    _apply_phase3_migration(_engine)
    _apply_phase5_migration(_engine)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    SessionLocal = _init()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

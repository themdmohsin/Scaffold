"""Database session management for the Scaffold engine.

The engine is created lazily so the app can boot (and serve /health) before
DATABASE_URL exists — e.g. right after cloning, before Supabase is set up.

Startup applies the ordered, idempotent migration manifest via
app/db/migrations.py (schema_migrations bookkeeping). Failures are reported and
never block boot, exactly like the previous per-file `_apply_x_migration`
helpers — but now every applied version, checksum, and skipped statement is
recorded and visible through `python -m app.scripts.migrate --status` and GET /ready.
"""

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.migrations import (  # noqa: F401  (_iter_sql_statements re-export kept for compatibility)
    apply_migration,
    iter_sql_statements as _iter_sql_statements,
    migration_by_filename,
    run_migrations_for_startup,
)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_migrations_applied = False


def normalized_database_url() -> str:
    """DATABASE_URL with the postgres:// / postgresql:// schemes normalized onto
    the psycopg 3 driver. Raises RuntimeError with an actionable message when unset."""
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set — copy engine/.env.example to engine/.env "
            "and paste the Supabase pooler connection string "
            "(Supabase → Project Settings → Database → Connection string)."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _apply_phase2_migration(engine: Engine) -> None:
    """Compatibility wrapper used by tests/test_phase2.py.

    Applies migrate_phase2.sql directly and does NOT touch schema_migrations, so
    the harness keeps exercising the SQL file's own idempotency.
    """
    apply_migration(engine, migration_by_filename("migrate_phase2.sql"), record=False)


def _init(apply_migrations: bool = True) -> sessionmaker:
    """Create (once) and return the session factory.

    `apply_migrations=False` is for tooling that manages migrations itself
    (app/scripts/migrate.py): it creates the connection pool without triggering
    the startup run.
    """
    global _engine, _SessionLocal, _migrations_applied
    if _SessionLocal is not None:
        if apply_migrations and not _migrations_applied and _engine is not None:
            run_migrations_for_startup(_engine)
            _migrations_applied = True
        return _SessionLocal

    url = normalized_database_url()
    _engine = create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args={"connect_timeout": 10},
    )
    if apply_migrations:
        run_migrations_for_startup(_engine)
        _migrations_applied = True
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

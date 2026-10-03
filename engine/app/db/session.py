"""Database session management for the Scaffold engine.

The engine is created lazily so the app can boot (and serve /health) before
DATABASE_URL exists — e.g. right after cloning, before Supabase is set up.
"""

import re
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


def _iter_sql_statements(sql_text: str) -> Iterator[str]:
    """Split a multi-statement SQL migration into individual statements.

    Splits on semicolons OUTSIDE of single-quoted strings, dollar-quoted blocks
    ($$...$$), line comments (-- …) and block comments (/* … */). Needed because
    psycopg3's simple query protocol rejects anything that even LOOKS like a
    client-side placeholder (%s/%I/%t) inside DO blocks' format()/RAISE bodies —
    so auth migration DO blocks were rewritten as plain statements, and this
    splitter lets each statement fail-and-skip on its own (e.g. RLS policies
    referencing Supabase's auth.uid() on a plain Postgres rig).
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql_text)
    state = "normal"  # normal | line_comment | block_comment | single | dollar
    dollar_tag = ""

    def flush() -> None:
        text_ = "".join(buf).strip()
        buf.clear()
        if text_:
            statements.append(text_)

    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""

        if state == "line_comment":
            buf.append(ch)
            if ch == "\n":
                state = "normal"
            i += 1
            continue
        if state == "block_comment":
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                state = "normal"
                continue
            i += 1
            continue
        if state == "single":
            buf.append(ch)
            if ch == "'":
                if nxt == "'":  # escaped quote
                    buf.append(nxt)
                    i += 2
                    continue
                state = "normal"
            i += 1
            continue
        if state == "dollar":
            buf.append(ch)
            if sql_text.startswith(dollar_tag, i):
                buf.extend(dollar_tag[1:])
                i += len(dollar_tag)
                state = "normal"
            else:
                i += 1
            continue

        # normal state
        if ch == "-" and nxt == "-":
            state = "line_comment"
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            state = "block_comment"
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            state = "single"
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            m = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql_text[i:])
            if m:
                dollar_tag = m.group(0)
                state = "dollar"
                buf.append(ch)
                i += 1
                continue
        if ch == ";":
            flush()
            i += 1
            continue
        buf.append(ch)
        i += 1

    flush()
    return iter(statements)


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


def _apply_auth_migration(engine: Engine) -> None:
    """Idempotent Phase 6 DDL (migrate_auth.sql): accounts, project_members,
    personal_access_tokens, invites, users.account_id, and the Row Level
    Security policies that scope dashboard/anon reads to active members.

    Unlike the earlier migrations (single multi-statement blob), this one is
    applied statement-by-statement with per-statement tolerance: one failing
    statement (e.g. RLS policies that need Supabase's auth.uid() on a plain
    Postgres rig, or a publication that only exists in Supabase) is reported
    and skipped WITHOUT aborting the rest — same fail-open-boot philosophy as
    _apply_sql_migration, at statement granularity.
    """
    sql_text = Path(__file__).with_name("migrate_auth.sql").read_text(encoding="utf-8")
    applied = 0
    for stmt in _iter_sql_statements(sql_text):
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql(stmt)
                conn.commit()
            applied += 1
        except Exception as exc:  # noqa: BLE001 — per-statement tolerance
            print(
                f"[scaffold] auth migration statement skipped: "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )
    if applied:
        print(f"[scaffold] auth migration: {applied} statements applied")
    else:
        print("[scaffold] auth migration: nothing new to apply")


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
    _apply_auth_migration(_engine)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    SessionLocal = _init()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""Ordered, idempotent migration runner for the Scaffold engine.

Replaces the old "call _apply_x_migration() in session.py at startup" model with a
single ordered manifest recorded in a `schema_migrations` table:

    version | name | checksum | applied_at | execution_ms | skipped_statements

How it works (and why it is safe on existing databases):

1. `ensure_migrations_table()` creates `schema_migrations` if absent. It is an
   ENGINE-MANAGED bookkeeping table (like Rails/Django/Alembic would use) — not
   part of the frozen product schema in docs/SCHEMA.md's product tables.
2. `run_pending()` walks MIGRATIONS in order and applies every version with no
   row yet. All SQL files are written to be idempotent (IF NOT EXISTS / DROP IF
   EXISTS / DO-blocks), so adopting an EXISTING database (created before this
   runner existed, with no `schema_migrations` rows) replays the whole manifest
   as a no-op and records it — no renames, no drops, no data loss.
3. Checksums are recorded per file. If an already-applied file changes on disk,
   the runner logs a loud warning (and `--status` shows it) but never re-runs
   the file — forward-only, same as every production migration tool.
4. `tolerant=True` migrations are applied statement-by-statement: a statement
   that needs Supabase-only pieces (auth.uid(), the anon/authenticated roles,
   the realtime publication) is reported and skipped WITHOUT aborting the
   remaining statements. This is the pre-existing fail-open boot behavior, now
   recorded (skipped count) instead of silently printed. On a real Supabase
   project nothing should skip — `bootstrap_db` runs strict there.
5. A tolerant migration whose EVERY statement fails is an error (nothing was
   created), so a totally-misconfigured run cannot masquerade as "applied".

The engine still boots if migrations fail: startup calls
`run_migrations_for_startup()`, which reports the problem and continues, exactly
like the old `_apply_sql_migration` did. Deployments get honest visibility from
`GET /ready` (503 while any migration is pending) and
`python -m app.scripts.migrate --status`.

Docs: docs/SCHEMA.md (schema_migrations), docs/OPERATIONS.md (runbook),
docs/DEPLOYMENT.md (bootstrap path).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InterfaceError, OperationalError

logger = logging.getLogger("scaffold.migrations")

MIGRATIONS_TABLE = "schema_migrations"


@dataclass(frozen=True)
class Migration:
    """One ordered, idempotent SQL file in the engine's migration manifest."""

    version: str  # zero-padded, ordering is lexical ("0001" < "0002" < ...)
    name: str
    filename: str  # relative to engine/app/db/
    tolerant: bool = False  # per-statement skip instead of all-or-nothing
    description: str = ""


# THE ORDER IS FROZEN ONCE APPLIED. Append new migrations with the next
# version; never reorder or edit an applied file's meaning (a checksum change
# is reported as a warning by run_pending / --status).
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "0001",
        "baseline",
        "schema.sql",
        description="Day 1 baseline: projects/users/tasks/decisions/contracts/commits/blockers/events",
    ),
    Migration(
        "0002",
        "day3_pgvector",
        "migrate_day3.sql",
        description="pgvector extension + embedding columns + realtime publication",
    ),
    Migration(
        "0003",
        "phase2_task_board",
        "migrate_phase2.sql",
        description="task board columns, dependencies, blockers, realtime additions",
    ),
    Migration(
        "0004",
        "phase3_team",
        "migrate_phase3.sql",
        description="agent identity, membership status, project ownership",
    ),
    Migration(
        "0005",
        "phase5_secure_environment",
        "migrate_phase5.sql",
        description="environment metadata/grants + scaffold_secrets keyring and ciphertext",
    ),
    Migration(
        "0006",
        "phase6_auth",
        "migrate_auth.sql",
        tolerant=True,
        description="accounts, project_members, PATs, invites, RLS policies, realtime",
    ),
)

_MANIFEST_BY_FILENAME = {m.filename: m for m in MIGRATIONS}
_MANIFEST_BY_VERSION = {m.version: m for m in MIGRATIONS}


class MigrationError(RuntimeError):
    """A migration failed in a way that must not be recorded as applied."""


@dataclass
class MigrationResult:
    version: str
    name: str
    status: str  # applied | skipped_statements | failed
    statements_applied: int = 0
    statements_skipped: int = 0
    skipped_details: list[str] = field(default_factory=list)
    execution_ms: int = 0
    checksum: str = ""

    def summary(self) -> str:
        if self.statements_skipped:
            return (
                f"{self.version}_{self.name}: applied, "
                f"{self.statements_skipped} statement(s) skipped (Supabase-only?), "
                f"{self.execution_ms}ms"
            )
        return f"{self.version}_{self.name}: applied ({self.execution_ms}ms)"


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent


def migration_path(migration: Migration) -> Path:
    return migrations_dir() / migration.filename


def migration_checksum(migration: Migration) -> str:
    return hashlib.sha256(migration_path(migration).read_bytes()).hexdigest()


def migration_by_filename(filename: str) -> Migration:
    try:
        return _MANIFEST_BY_FILENAME[filename]
    except KeyError:  # pragma: no cover - programmer error
        raise MigrationError(f"{filename} is not part of the migration manifest") from None


# ---------------------------------------------------------------------------
# SQL statement splitting (moved here from app/db/session.py)
# ---------------------------------------------------------------------------
def iter_sql_statements(sql_text: str) -> list[str]:
    """Split a multi-statement SQL migration into individual statements.

    Splits on semicolons OUTSIDE of single-quoted strings, dollar-quoted blocks
    ($$...$$), line comments (-- ...) and block comments (/* ... */). Needed
    because psycopg3's simple query protocol rejects anything that even LOOKS
    like a client-side placeholder (%s/%I/%t) inside DO blocks' format()/RAISE
    bodies — so auth migration DO blocks were rewritten as plain statements,
    and this splitter lets each statement fail-and-skip on its own (e.g. RLS
    policies referencing Supabase's auth.uid() on a plain Postgres rig).
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql_text)
    state = "normal"  # normal | line_comment | block_comment | single | dollar
    dollar_tag = ""

    def flush() -> None:
        flushed = "".join(buf).strip()
        buf.clear()
        if flushed:
            statements.append(flushed)

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
    return statements


# ---------------------------------------------------------------------------
# schema_migrations bookkeeping
# ---------------------------------------------------------------------------
def ensure_migrations_table(engine: Engine) -> None:
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
      version             TEXT PRIMARY KEY,
      name                TEXT NOT NULL,
      checksum            TEXT NOT NULL,
      applied_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
      execution_ms        INTEGER NOT NULL DEFAULT 0,
      skipped_statements  INTEGER NOT NULL DEFAULT 0
    )
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)


def load_applied(engine: Engine) -> dict[str, dict]:
    """version -> recorded row. Empty when the table does not exist yet."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT version, name, checksum, applied_at, execution_ms, skipped_statements "
                    f"FROM {MIGRATIONS_TABLE}"
                )
            ).fetchall()
    except Exception:
        return {}
    return {
        str(r[0]): {
            "name": r[1],
            "checksum": r[2],
            "applied_at": r[3],
            "execution_ms": r[4],
            "skipped_statements": r[5],
        }
        for r in rows
    }


def _record_applied(engine: Engine, result: MigrationResult) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {MIGRATIONS_TABLE}
                    (version, name, checksum, applied_at, execution_ms, skipped_statements)
                VALUES (:version, :name, :checksum, now(), :execution_ms, :skipped)
                ON CONFLICT (version) DO UPDATE
                   SET checksum = EXCLUDED.checksum,
                       applied_at = EXCLUDED.applied_at,
                       execution_ms = EXCLUDED.execution_ms,
                       skipped_statements = EXCLUDED.skipped_statements
                """
            ),
            {
                "version": result.version,
                "name": result.name,
                "checksum": result.checksum,
                "execution_ms": result.execution_ms,
                "skipped": result.statements_skipped,
            },
        )


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def apply_migration(engine: Engine, migration: Migration, *, record: bool = True) -> MigrationResult:
    """Apply one migration file. Raises MigrationError when the file fails.

    Non-tolerant files run as one script (all-or-nothing). Tolerant files run
    statement-by-statement with per-statement reporting; a run where NOTHING
    succeeded raises instead of recording a false 'applied'.
    """
    checksum = migration_checksum(migration)
    sql_text = migration_path(migration).read_text(encoding="utf-8")
    started = time.perf_counter()
    applied = 0
    skipped = 0
    skipped_details: list[str] = []

    if migration.tolerant:
        statements = iter_sql_statements(sql_text)
        for stmt in statements:
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(stmt)
                applied += 1
            except (OperationalError, InterfaceError) as exc:
                # Connection trouble (pooler drop, DNS blip) is NOT a
                # "Supabase-only statement": abort WITHOUT recording, so the
                # next boot / `migrate` run retries idempotently instead of
                # silently marking the migration applied with a hole.
                raise MigrationError(
                    f"migration {migration.version}_{migration.name} lost its database connection "
                    f"while applying a statement: {str(exc)[:200]}. Nothing was recorded for this "
                    "migration — re-run `python -m app.scripts.migrate` when the database is reachable."
                ) from exc
            except Exception as exc:  # noqa: BLE001 — documented per-statement tolerance
                skipped += 1
                first_line = stmt.strip().splitlines()[0][:120] if stmt.strip() else "(empty)"
                detail = f"{first_line} -> {type(exc).__name__}: {str(exc)[:200]}"
                skipped_details.append(detail)
                logger.warning(
                    "migration %s_%s statement skipped: %s", migration.version, migration.name, detail
                )
        if applied == 0 and skipped:
            raise MigrationError(
                f"migration {migration.version}_{migration.name} applied 0 of {skipped} statements — "
                "the database needs the extensions/roles this migration expects "
                "(run `python -m app.scripts.bootstrap_db` or see docs/DEPLOYMENT.md)"
            )
    else:
        try:
            with engine.begin() as conn:
                # No bound params -> psycopg 3 simple query protocol -> multi-statement OK.
                conn.exec_driver_sql(sql_text)
            applied = 1
        except Exception as exc:  # noqa: BLE001 — wrapped with version context
            raise MigrationError(
                f"migration {migration.version}_{migration.name} failed: "
                f"{type(exc).__name__}: {str(exc)[:500]}"
            ) from exc

    execution_ms = int((time.perf_counter() - started) * 1000)
    result = MigrationResult(
        version=migration.version,
        name=migration.name,
        status="skipped_statements" if skipped else "applied",
        statements_applied=applied,
        statements_skipped=skipped,
        skipped_details=skipped_details,
        execution_ms=execution_ms,
        checksum=checksum,
    )
    if record:
        _record_applied(engine, result)
    return result


def run_pending(
    engine: Engine,
    *,
    strict: bool = False,
    retry_skipped: bool = False,
) -> list[MigrationResult]:
    """Apply every manifest migration without a schema_migrations row.

    `strict=True` raises MigrationError if any statement was skipped by this run
    (used by bootstrap against a real Supabase project, where nothing should
    skip). `retry_skipped=True` re-applies previously-applied tolerant
    migrations that recorded skipped statements (e.g. you moved a dev database
    onto a Supabase-shaped one and want the RLS policies now).
    """
    ensure_migrations_table(engine)
    applied = load_applied(engine)
    results: list[MigrationResult] = []

    for migration in MIGRATIONS:
        recorded = applied.get(migration.version)
        if recorded is None:
            result = apply_migration(engine, migration)
            results.append(result)
            logger.info("%s", result.summary())
            continue

        try:
            current = migration_checksum(migration)
        except OSError:
            continue
        if current != recorded["checksum"]:
            logger.warning(
                "migration %s_%s file changed after it was applied (recorded checksum %s…, "
                "current %s…) — the runner does not re-run applied migrations; add a new "
                "migration instead of editing history",
                migration.version,
                migration.name,
                str(recorded["checksum"])[:12],
                current[:12],
            )
        if retry_skipped and migration.tolerant and recorded.get("skipped_statements"):
            result = apply_migration(engine, migration)
            results.append(result)
            logger.info(
                "re-applied %s_%s to retry %s previously skipped statement(s): %s now skipped",
                migration.version,
                migration.name,
                recorded["skipped_statements"],
                result.statements_skipped,
            )

    if strict:
        offenders = [r for r in results if r.statements_skipped]
        if offenders:
            details = "; ".join(r.skipped_details[0] for r in offenders if r.skipped_details)
            raise MigrationError(
                f"strict mode: {len(offenders)} migration(s) skipped statements — {details}"
            )
    return results


def migration_status(engine: Engine) -> dict:
    """Read-only snapshot for /ready and `migrate --status`."""
    ensure_migrations_table(engine)
    applied = load_applied(engine)
    applied_rows = []
    pending_rows = []
    checksum_mismatches = []
    for version, row in sorted(applied.items()):
        applied_rows.append({"version": version, **{k: v for k, v in row.items()}})
        migration = _MANIFEST_BY_VERSION.get(version)
        if migration is None:
            continue
        if row["checksum"] != migration_checksum(migration):
            checksum_mismatches.append(version)
    for migration in MIGRATIONS:
        if migration.version not in applied:
            pending_rows.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "description": migration.description,
                }
            )
    unknown_applied = [v for v in sorted(applied) if v not in _MANIFEST_BY_VERSION]
    return {
        "applied": applied_rows,
        "pending": pending_rows,
        "unknown_applied": unknown_applied,
        "checksum_mismatches": checksum_mismatches,
    }


def run_migrations_for_startup(engine: Engine) -> bool:
    """Startup entry point: never raises, never blocks the engine from booting.

    Returns True when migrations ran (or were already current) without a
    recorded problem; False when the database could not be migrated. Details go
    to the structured log and the `schema_migrations`/`--status` surfaces.
    """
    try:
        results = run_pending(engine)
    except Exception as exc:  # noqa: BLE001 — fail-open boot, same as the old model
        logger.warning("migration startup warning (engine continues to boot): %s", exc)
        return False
    skipped = sum(r.statements_skipped for r in results)
    if results:
        logger.info(
            "migrations: %s applied (%s statement(s) skipped — see docs/OPERATIONS.md)",
            len(results),
            skipped,
        )
    else:
        logger.info("migrations: schema up to date")
    return True

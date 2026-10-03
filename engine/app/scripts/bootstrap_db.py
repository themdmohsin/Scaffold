"""Bootstrap a brand-new database end-to-end (Supabase or any Postgres+pgvector).

Usage (from engine/, with DATABASE_URL in engine/.env or the environment):

    python -m app.scripts.bootstrap_db            # strict: fail on skipped statements
    python -m app.scripts.bootstrap_db --lenient  # tolerate Supabase-only statements
    python -m app.scripts.bootstrap_db --status   # verify only, change nothing

What it does, in order:

  1. Connects and prints the target host/database (NEVER credentials).
  2. Ensures the required extensions: `pgcrypto` (secret-store keyring) and
     `vector` (embeddings). On Supabase both already exist; if this role cannot
     create one, you get the exact SQL to run in the SQL editor.
  3. Applies the ordered migration manifest (schema.sql + every migrate_*.sql)
     through app/db/migrations.py, recording versions/checksums/skips in
     `schema_migrations`. Strict by default: a real Supabase project should skip
     NOTHING. `--lenient` keeps going on Supabase-only statements (plain-Postgres
     rigs where auth.uid()/anon/authenticated don't exist).
  4. Verifies the `supabase_realtime` publication exists and carries every table
     the dashboard subscribes to; repairs missing membership when possible.
  5. Verifies critical tables exist and the secret-store keyring has a row.
  6. Prints an honest summary plus the next steps (JWT secret, first sign-in).

Exit codes: 0 ok / 1 database or migration failure / 2 configuration error.
This is safe to re-run: every step is idempotent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

EXTENSION_HINTS = {
    "vector": (
        "Postgres+pgvector >= 0.5 is required for embeddings.",
        "Supabase > Database > Extensions > enable 'vector'. "
        "Or run in the SQL editor: create extension if not exists vector;",
    ),
    "pgcrypto": (
        "pgcrypto is required for the secret-store keyring (gen_random_bytes).",
        "Supabase > Database > Extensions > enable 'pgcrypto'. "
        "Or run in the SQL editor: create extension if not exists pgcrypto;",
    ),
}

# Tables the dashboard subscribes to via Supabase Realtime (docs/SCHEMA.md).
REALTIME_TABLES = [
    "tasks",
    "decisions",
    "events",
    "blockers",
    "task_dependencies",
    "users",
    "environment_variables",
    "environment_access",
    "invites",
]

CRITICAL_TABLES = [
    "projects",
    "users",
    "tasks",
    "task_dependencies",
    "decisions",
    "decision_affects_tasks",
    "api_contracts",
    "commits",
    "blockers",
    "events",
    "accounts",
    "project_members",
    "personal_access_tokens",
    "invites",
    "invite_redemptions",
    "environment_variables",
    "environment_access",
]


class BootstrapError(RuntimeError):
    pass


def _redacted_target(database_url: str) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    host = url.host or "?"
    port = f":{url.port}" if url.port else ""
    return f"{host}{port}/{url.database or '?'}"


def _ensure_extensions(engine, *, lenient: bool) -> list[str]:
    problems: list[str] = []
    for ext in ("pgcrypto", "vector"):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            print(f"  ok: extension {ext}")
        except Exception as exc:  # noqa: BLE001 — reported with exact recovery steps
            reason, fix = EXTENSION_HINTS[ext]
            message = f"extension {ext} is missing and could not be created: {str(exc)[:200]}"
            if lenient:
                problems.append(f"{message}\n    {reason}\n    Fix: {fix}")
                print(f"  ! {message} (--lenient: continuing)")
            else:
                raise BootstrapError(f"{message}\n  {reason}\n  Fix: {fix}") from exc
    return problems


def _ensure_realtime_publication(engine) -> list[str]:
    """Create/verify the supabase_realtime publication membership."""
    warnings: list[str] = []
    with engine.connect() as conn:
        pub_exists = conn.execute(
            text("SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'")
        ).first()
    if not pub_exists:
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("CREATE PUBLICATION supabase_realtime")
            print("  ok: publication supabase_realtime created")
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"could not create publication supabase_realtime: {str(exc)[:160]}\n"
                "    On Supabase it is created automatically. Or run in the SQL editor:\n"
                "      create publication supabase_realtime;"
            )
            return warnings

    with engine.connect() as conn:
        members = {
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_publication_tables WHERE pubname = 'supabase_realtime'")
            ).fetchall()
        }
    missing = [t for t in REALTIME_TABLES if t not in members]
    for table in missing:
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(f"ALTER PUBLICATION supabase_realtime ADD TABLE public.{table}")
            print(f"  ok: realtime publication += {table}")
        except Exception as exc:  # noqa: BLE001 — duplicates/privileges
            warnings.append(f"could not add {table} to supabase_realtime: {str(exc)[:160]}")
    if not missing:
        print(f"  ok: realtime publication covers all {len(REALTIME_TABLES)} tables")
    return warnings


def _verify_schema(engine) -> list[str]:
    problems: list[str] = []
    with engine.connect() as conn:
        missing = []
        for table in CRITICAL_TABLES:
            found = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}).scalar()
            if found is None:
                missing.append(table)
        if missing:
            problems.append("missing tables after migrations: " + ", ".join(missing))

        keyring = conn.execute(
            text("SELECT count(*) FROM scaffold_secrets.keyring")
        ).scalar()
        if not keyring:
            problems.append(
                "scaffold_secrets.keyring has no row — the secret store would fail closed. "
                "Re-run bootstrap (migration 0005 bootstraps a 'default' key)."
            )
        else:
            print(f"  ok: secret-store keyring present ({keyring} key(s))")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.bootstrap_db",
        description="Bootstrap a brand-new database: extensions, schema, migrations, realtime publication.",
    )
    parser.add_argument("--lenient", action="store_true", help="continue when Supabase-only pieces are missing")
    parser.add_argument("--status", action="store_true", help="verify only — apply nothing")
    args = parser.parse_args(argv)

    try:
        from app.config import settings

        if not settings.database_url:
            print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
            print(
                "  Copy engine/.env.example to engine/.env and paste the Supabase connection string\n"
                "  (Supabase > Project Settings > Database > Connection string, use the pooler"
                " string with the postgresql:// prefix).",
                file=sys.stderr,
            )
            return 2

        from app.db.session import _init, normalized_database_url

        print(f"-> bootstrapping {_redacted_target(normalized_database_url())}")
        SessionLocal = _init(apply_migrations=False)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: configuration problem: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        db = SessionLocal()
        try:
            engine = db.get_bind()

            # 1. Connection sanity.
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: cannot connect to the database: {str(exc)[:300]}", file=sys.stderr)
                print(
                    "  Check DATABASE_URL and that the database accepts connections from this machine\n"
                    "  (Supabase free projects pause when idle; the pooler string is usually the right one).",
                    file=sys.stderr,
                )
                return 1
            print("  ok: connected")

            warnings: list[str] = []

            if args.status:
                from app.db.migrations import migration_status

                status = migration_status(engine)
                missing_tables = _verify_schema(engine)
                print("  status only (--status): no changes applied")
                for row in status["pending"]:
                    print(f"  ! pending migration {row['version']}_{row['name']}")
                problems = [w for w in warnings if w] + missing_tables
                for problem in problems:
                    print(f"  ! {problem}")
                return 1 if (problems or status["pending"]) else 0

            # 2. Extensions (before migrations: pgcrypto powers the keyring, vector the embeddings).
            warnings.extend(_ensure_extensions(engine, lenient=args.lenient))

            # 3. Migrations.
            from app.db.migrations import MigrationError, run_pending

            try:
                results = run_pending(engine, strict=not args.lenient)
            except MigrationError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            for result in results:
                print(f"  ok: {result.summary()}")
            if not results:
                print("  ok: migrations already applied (schema up to date)")

            # 4. Realtime publication.
            warnings.extend(_ensure_realtime_publication(engine))

            # 5. Verify.
            problems = [w for w in warnings if w] + _verify_schema(engine)
            if problems:
                print("\\nWARNINGS:")
                for problem in problems:
                    print(f"  ! {problem}")

            print("\\nBootstrap complete.")
            print("Next steps:")
            print("  1. Set SUPABASE_JWT_SECRET in engine/.env (Supabase > Settings > API > JWT Secret).")
            print("  2. Start the engine: uvicorn app.main:app  (docker compose up -d engine)")
            print("  3. Sign in through Supabase Auth, mint a PAT via POST /auth/tokens, then")
            print("     point teammates at this engine (docs/DEPLOYMENT.md).")
            print("  Verify any time:  python -m app.scripts.migrate --status  and  GET /ready")
            return 0
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — top-level guard with clear message
        print(f"ERROR: bootstrap failed: {type(exc).__name__}: {str(exc)[:400]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

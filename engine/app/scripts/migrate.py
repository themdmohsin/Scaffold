"""Ordered migration CLI for the Scaffold engine.

Usage (from engine/):

    python -m app.scripts.migrate                 # apply every pending migration
    python -m app.scripts.migrate --status        # show applied/pending + checksums
    python -m app.scripts.migrate --check         # CI/deploy gate: exit 1 if pending
    python -m app.scripts.migrate --strict        # fail if any statement was skipped
    python -m app.scripts.migrate --retry-skipped # re-apply tolerant migrations
                                                  # that recorded skipped statements

The same runner executes automatically at engine startup (never blocks boot);
this CLI is for deploys, CI gates, and inspecting state. It creates the
connection pool WITHOUT triggering the startup run, then applies the manifest
itself so failures surface as a non-zero exit code.

Exit codes: 0 ok / 1 migration or connection failure / 2 configuration error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.engine import make_url  # noqa: E402


def _redacted_target(database_url: str) -> str:
    """host:port/database (never credentials) for log/CLI output."""
    try:
        url = make_url(database_url)
        host = url.host or "?"
        port = f":{url.port}" if url.port else ""
        return f"{host}{port}/{url.database or '?'}"
    except Exception:  # noqa: BLE001 — malformed URL reported elsewhere
        return "(unparseable DATABASE_URL)"


def _print_status(status: dict) -> None:
    print("Applied migrations (schema_migrations):")
    if not status["applied"]:
        print("  (none)")
    for row in status["applied"]:
        skipped = row.get("skipped_statements") or 0
        flag = f"  skipped={skipped}" if skipped else ""
        print(f"  {row['version']}_{row['name']:<28} {row['applied_at']}{flag}")
    print("Pending migrations:")
    if not status["pending"]:
        print("  (none — schema up to date)")
    for row in status["pending"]:
        print(f"  {row['version']}_{row['name']:<28} {row['description']}")
    if status["checksum_mismatches"]:
        print(
            "WARNING: these applied migration files changed on disk (runner will NOT re-run them; "
            "add a new migration instead): " + ", ".join(status["checksum_mismatches"])
        )
    if status["unknown_applied"]:
        print(
            "WARNING: the database has schema_migrations rows this engine build does not know "
            "(newer engine deploy?): " + ", ".join(status["unknown_applied"])
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.migrate",
        description="Apply/inspect the ordered, idempotent Scaffold migration manifest.",
    )
    parser.add_argument("--status", action="store_true", help="list applied/pending migrations and exit")
    parser.add_argument("--check", action="store_true", help="exit 1 when migrations are pending")
    parser.add_argument("--strict", action="store_true", help="fail when any statement is skipped")
    parser.add_argument(
        "--retry-skipped",
        action="store_true",
        help="re-apply tolerant migrations that recorded skipped statements",
    )
    args = parser.parse_args(argv)

    try:
        from app.config import settings, validate_settings

        report = validate_settings()
        if not settings.database_url:
            print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
            for err in report.errors:
                print(f"  - {err}", file=sys.stderr)
            return 2

        from app.db.migrations import MigrationError, migration_status, run_pending
        from app.db.session import _init, normalized_database_url

        target = _redacted_target(normalized_database_url())
        print(f"-> target {target}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: configuration problem: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    SessionLocal = None
    try:
        SessionLocal = _init(apply_migrations=False)
        db = SessionLocal()
        try:
            engine = db.get_bind()
            if args.status:
                _print_status(migration_status(engine))
                return 0
            if args.check:
                status = migration_status(engine)
                if status["pending"]:
                    print(
                        f"ERROR: {len(status['pending'])} pending migration(s): "
                        + ", ".join(f"{r['version']}_{r['name']}" for r in status["pending"]),
                        file=sys.stderr,
                    )
                    return 1
                print("migrations: up to date")
                return 0

            results = run_pending(engine, strict=args.strict, retry_skipped=args.retry_skipped)
            if not results:
                print("migrations: nothing to apply (schema up to date)")
            for result in results:
                print(f"  ok: {result.summary()}")
            return 0
        finally:
            db.close()
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "  Hint: run `python -m app.scripts.bootstrap_db` for a brand-new database, or see "
            "docs/DEPLOYMENT.md.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not apply migrations: {type(exc).__name__}: {str(exc)[:400]}", file=sys.stderr)
        print(
            "  Check DATABASE_URL (Supabase > Project Settings > Database) and that the database "
            "is reachable from this machine.",
            file=sys.stderr,
        )
        return 1
    finally:
        # Drop the pool so short-lived CLI processes exit promptly.
        if SessionLocal is not None:
            try:
                bind = getattr(SessionLocal, "kw", {}).get("bind")
                if bind is not None:
                    bind.dispose()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())

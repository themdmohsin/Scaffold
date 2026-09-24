"""Backfill embeddings for existing decisions + api_contracts (Day 3).

Idempotent: rows with a non-null embedding are skipped, so it is safe to
re-run after adding more data. Requires SCAFFOLD_TEAM_LLM_KEY in engine/.env.

Usage (from engine/):
    python -m app.scripts.backfill_embeddings [--project <uuid>] [--force]

--force re-embeds rows even if they already have an embedding.
"""

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.models import ApiContract, Decision, Project  # noqa: E402
from app.db.session import _init  # noqa: E402
from app.services import retrieval  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill pgvector embeddings")
    parser.add_argument("--project", help="limit to one project UUID")
    parser.add_argument("--force", action="store_true", help="re-embed rows that already have an embedding")
    args = parser.parse_args()

    project_filter = uuid.UUID(args.project) if args.project else None
    if project_filter:
        if not _init()().get(Project, project_filter):
            print(f"project {project_filter} not found")
            sys.exit(1)

    SessionLocal = _init()
    db = SessionLocal()
    try:
        dec_q = db.query(Decision)
        con_q = db.query(ApiContract)
        if project_filter:
            dec_q = dec_q.filter(Decision.project_id == project_filter)
            con_q = con_q.filter(ApiContract.project_id == project_filter)
        if not args.force:
            dec_q = dec_q.filter(Decision.embedding.is_(None))
            con_q = con_q.filter(ApiContract.embedding.is_(None))

        decisions = dec_q.order_by(Decision.created_at.asc()).all()
        contracts = con_q.order_by(ApiContract.created_at.asc()).all()

        print(f"embedding {len(decisions)} decision(s), {len(contracts)} contract(s)...")
        ok = fail = 0
        for d in decisions:
            before = d.embedding
            retrieval.embed_decision_row(d)
            if d.embedding is not None:
                db.add(d)
                ok += 1
            else:
                d.embedding = before
                fail += 1
        cok = cfail = 0
        for c in contracts:
            before = c.embedding
            retrieval.embed_contract_row(c)
            if c.embedding is not None:
                db.add(c)
                cok += 1
            else:
                c.embedding = before
                cfail += 1
        db.commit()
        print(f"decisions: {ok} embedded, {fail} failed")
        print(f"contracts: {cok} embedded, {cfail} failed")
        if fail or cfail:
            print("some rows failed — check SCAFFOLD_TEAM_LLM_KEY / provider quota, then re-run")
            sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()

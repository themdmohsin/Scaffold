"""Conflict recorder — turns detection findings into shared state + notification.

Day 4 Part B glue used by BOTH contract entry points:
  - POST /projects/:id/contracts           (plugin "after" hook, dashboard)
  - POST /projects/:id/github-webhook      (push → parse_diff → new contracts)

For every conflict/warning finding it:
  1. writes a `conflict_flagged` event (audit trail; dashboard reads events)
  2. writes a `blockers` row, deduped against unresolved identical blockers
  3. schedules the auto-GitHub-issue (fire-and-forget, fail-open — see
     services/github_issues.py)

Detection itself stays pure in conflict_service.py (repo rule #3); this module
only persists deterministic facts — never an LLM opinion.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Blocker, Event
from app.services import github_issues


def _blocker_description(finding: dict) -> str:
    inc, ex = finding.get("incoming", {}), finding.get("existing", {})
    kind = finding.get("kind", "conflict").replace("_", " ")
    return (
        f"[{finding.get('severity', 'conflict').upper()}] {kind}: "
        f"incoming {inc.get('method', '?')} {inc.get('route', '?')} vs registered "
        f"{ex.get('method', '?')} {ex.get('route', '?')}"
    )


def record_conflicts(
    db: Session,
    project_id: uuid.UUID,
    findings: list[dict],
    incoming_source: str,
) -> dict:
    """Persist events + blockers for the given findings and schedule issue creation.

    Returns a small summary dict ({events, blockers, issues}) that callers can
    surface in API responses/logs. Never raises for individual finding
    failures — ingestion must keep working (fail-open).
    """
    summary = {"events": 0, "blockers": 0, "issues": 0}
    for f in findings:
        payload = {
            "kind": f.get("kind"),
            "severity": f.get("severity"),
            "existing": f.get("existing"),
            "incoming": f.get("incoming"),
            "differing_fields": f.get("differing_fields"),
            "source": incoming_source,
        }
        db.add(Event(project_id=project_id, type="conflict_flagged", payload=payload))
        summary["events"] += 1

        description = _blocker_description(f)
        open_dup = db.scalars(
            select(Blocker).where(
                Blocker.project_id == project_id,
                Blocker.description == description,
                Blocker.resolved.is_(False),
            )
        ).first()
        if not open_dup:
            db.add(Blocker(project_id=project_id, description=description, resolved=False))
            summary["blockers"] += 1

        try:
            github_issues.spawn_issue_creation(f, context={"incoming_source": incoming_source})
            summary["issues"] += 1
        except Exception:
            pass  # issue action is best-effort by contract

    return summary

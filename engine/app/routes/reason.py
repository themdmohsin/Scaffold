"""POST /projects/:id/reason — the umbrella reasoning endpoint (frozen shape).

Builds a small, targeted context block (repo rule #6): the always-on summary
plus retrieval hits for this specific prompt, then asks the LLM (reasoning.py
only) for {answer, suggested_tasks}.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.routes.context import build_context
from app.services import availability, reasoning, retrieval

router = APIRouter(tags=["reason"])

MAX_CONTEXT_CHARS = 4000  # repo rule #6: small and targeted, never a project dump


class ReasonRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


def _render_context(context: dict, hits: dict, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Compact text rendering of the always-on summary + retrieval hits."""
    lines: list[str] = []
    p = context["project"]
    lines.append(f"PROJECT: {p['name']} — goal: {p.get('goal') or 'n/a'}")
    if p.get("deadline"):
        lines.append(f"DEADLINE: {p['deadline']}")

    t = context["tasks"]
    lines.append(f"TASKS: {t['todo']} todo, {t['in_progress']} in progress, {t['done']} done")
    if context["active_tasks"]:
        lines.append("ACTIVE TASKS:")
        for task in context["active_tasks"]:
            due = f" (due {task['due_at']})" if task.get("due_at") else ""
            lines.append(f"- [{task['status']}] {task['title']}{due}")

    seen: set[str] = set()
    decisions = hits.get("decisions", []) + [
        d for d in context.get("recent_decisions", []) if d["id"] not in {h["id"] for h in hits.get("decisions", [])}
    ]
    if decisions:
        lines.append("DECISIONS:")
        for d in decisions:
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            why = f" — because: {d['reasoning']}" if d.get("reasoning") else ""
            lines.append(f"- {d['text']}{why}")

    contracts: list[dict] = []
    seen_c: set[tuple[str, str]] = set()
    for c in hits.get("contracts", []):
        key = (c["method"], c["route"])
        if key not in seen_c:
            seen_c.add(key)
            contracts.append(c)
    for c in context.get("relevant_contracts", []):
        key = (c["method"], c["route"])
        if key not in seen_c:
            seen_c.add(key)
            contracts.append(c)
    if contracts:
        lines.append("API CONTRACTS:")
        for c in contracts:
            lines.append(f"- {c['method']} {c['route']}")

    block = "\n".join(lines)
    return block[:max_chars]


@router.post("/projects/{project_id}/reason")
def reason(project_id: uuid.UUID, body: ReasonRequest, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")

    context = build_context(db, project_id)
    hits = retrieval.retrieve_for_prompt(db, project_id, body.prompt)

    # Day 5 (Person A): deterministic availability facts + deterministic
    # re-validation of whatever the LLM suggests. Repo rule #2 — the LLM only
    # chooses among roster entries the SQL already computed; it never invents
    # owners or dates, and nothing unvalidated reaches the client.
    project = db.get(Project, project_id)
    now = datetime.now(timezone.utc)
    roster_rows = availability.roster(db, project_id)
    roster_block = availability.render_roster_block(roster_rows, project.deadline, now)
    # Repo rule #6: the COMBINED block is bounded, not just each half. The roster
    # is the Day 5 payload and stays whole (bounded by MAX_ROSTER_ROWS); the
    # always-on summary yields the remaining room.
    base_block = _render_context(context, hits)
    room = max(0, MAX_CONTEXT_CHARS - len(roster_block) - 2)
    context_block = (base_block[:room] + "\n\n" + roster_block) if room else roster_block

    try:
        result = reasoning.answer_prompt(context_block, body.prompt)
    except reasoning.LlmUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # provider outage / malformed response upstream
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    result["suggested_tasks"], notes = availability.validate_assignments(
        result.get("suggested_tasks", []), roster_rows, now, project.deadline
    )
    if notes:
        result["assignment_notes"] = notes  # additive; frozen shape untouched
    return result

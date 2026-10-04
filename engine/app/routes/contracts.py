"""API contracts routes: GET + POST /projects/:id/contracts (frozen shapes).

POST is what the plugin's "after" hook calls to register a new contract
(master doc §4 step 7) and what the webhook uses implicitly via diff parsing.

Day 4 Part B: POST also runs deterministic conflict detection (rule #3) against
the project's registered contracts BEFORE inserting, recording conflict_flagged
events + blockers and scheduling the auto-GitHub-issue on a hit. Registration
still succeeds (201) — detection is a safety net, not a blocker.

Day 12 (client fork): POST /projects/:id/contracts/check — a read-only,
stateless pre-write check. The client fork sends the routes it is ABOUT to
write (extracted locally with the same deterministic regexes as diff_parser);
the engine compares them against registered contracts with the exact same
conflict_service used at registration time. Nothing is stored, no event is
written — the caller decides whether to warn or block.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiContract, Event, Project, Task
from app.db.session import get_db
from app.services import conflict_service, contract_check, retrieval
from app.services.auth import Principal, require_member, require_principal
from app.services.conflict_recorder import record_conflicts

router = APIRouter(prefix="/projects/{project_id}/contracts", tags=["contracts"])


class ContractCreate(BaseModel):
    route: str = Field(min_length=1)
    method: str = Field(min_length=1, pattern="^[A-Z]+$")
    request_schema: dict | None = None
    response_schema: dict | None = None
    created_by_task_id: uuid.UUID | None = None


class RouteHint(BaseModel):
    """One route a client is about to write (extracted client-side, never
    source text — repo rule #4 keeps file contents on the developer's machine)."""

    route: str = Field(min_length=1)
    method: str = Field(min_length=1, pattern="^[A-Za-z]+$")
    file: str | None = None
    request_schema: dict | None = None
    response_schema: dict | None = None


class ContractCheck(BaseModel):
    """Pre-write check body. All inputs optional; `routes` is the normal path
    for the client fork (local deterministic extraction), `content`/`diff` let
    HTTP callers hand raw text to the engine's own parser for the check only —
    it is never persisted, embedded, or logged."""

    file: str | None = None
    routes: list[RouteHint] | None = None
    content: str | None = Field(default=None, max_length=500_000)
    diff: str | None = Field(default=None, max_length=1_000_000)
    request_schema: dict | None = None
    response_schema: dict | None = None


def _out(c: ApiContract) -> dict:
    return {
        "id": str(c.id),
        "project_id": str(c.project_id) if c.project_id else None,
        "route": c.route,
        "method": c.method,
        "request_schema": c.request_schema,
        "response_schema": c.response_schema,
        "created_by_task_id": str(c.created_by_task_id) if c.created_by_task_id else None,
        "created_at": c.created_at.isoformat(),
    }


@router.get("")
def list_contracts(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_member(db, project_id, principal)
    rows = db.scalars(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.created_at.desc())
    ).all()
    return [_out(c) for c in rows]


@router.post("", status_code=201)
def create_contract(
    project_id: uuid.UUID,
    body: ContractCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    require_member(db, project_id, principal)
    if body.created_by_task_id:
        task = db.get(Task, body.created_by_task_id)
        if not task or task.project_id != project_id:
            raise HTTPException(status_code=400, detail="created_by_task_id is not a task on this project")

    # Day 4 Part B — deterministic conflict detection before the write.
    findings = conflict_service.detect_contract_conflicts(
        [
            {
                "route": body.route,
                "method": body.method.upper(),
                "request_schema": body.request_schema,
                "response_schema": body.response_schema,
            }
        ],
        [
            {
                "route": r.route,
                "method": r.method,
                "request_schema": r.request_schema,
                "response_schema": r.response_schema,
            }
            for r in db.scalars(select(ApiContract).where(ApiContract.project_id == project_id)).all()
        ],
    )
    conflict_summary = record_conflicts(db, project_id, findings, incoming_source="contracts API")

    c = ApiContract(
        project_id=project_id,
        route=body.route,
        method=body.method.upper(),
        request_schema=body.request_schema,
        response_schema=body.response_schema,
        created_by_task_id=body.created_by_task_id,
    )
    retrieval.embed_contract_row(c)  # Day 3 pgvector; fail open -> embedding null
    db.add(c)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            type="contract_registered",
            payload={"contract_id": str(c.id), "route": c.route, "method": c.method},
        )
    )
    db.commit()
    db.refresh(c)
    out = _out(c)
    if findings:  # additive response key (changelog-noted) so callers see the hit
        out["conflicts"] = conflict_summary
    return out


@router.post("/check")
def check_contracts(
    project_id: uuid.UUID,
    body: ContractCheck,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Deterministic PRE-WRITE check (Day 12, client fork).

    Answers "would this write collide with a registered contract?" without
    writing anything. The client fork calls this from tool.execute.before with
    the routes it is about to introduce; a `conflict` severity lets the fork
    block the write with the registered shape in the message. Warnings are
    advisory. Same pure detector as registration — no LLM, no heuristics, no
    state change (repo rule #3).

    The same implementation backs the MCP tool check_api_contracts(...) — see
    services/contract_check.py."""
    require_member(db, project_id, principal)
    incoming = contract_check.build_incoming(
        file=body.file,
        routes=[hint.model_dump() for hint in body.routes] if body.routes else None,
        content=body.content,
        diff=body.diff,
        request_schema=body.request_schema,
        response_schema=body.response_schema,
    )
    return contract_check.run_check(db, project_id, incoming)

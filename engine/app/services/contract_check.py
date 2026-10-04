"""Deterministic pre-write contract check (Day 12, client fork).

One implementation shared by:
  - POST /projects/:id/contracts/check   (HTTP; dashboard/other clients)
  - MCP tool check_api_contracts(...)    (the fork's plugin, over the agent's
    authenticated MCP connection)

Read-only and stateless: it compares route hints the caller is ABOUT to write
against the project's registered contracts using the SAME pure detector as
registration (services/conflict_service.py). Nothing is stored, no event is
written, and any content/diff handed in for parsing is never persisted,
embedded, or logged — only route names + methods are echoed back.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiContract
from app.services import conflict_service, diff_parser

MAX_CONTENT_CHARS = 500_000
MAX_DIFF_CHARS = 1_000_000


def build_incoming(
    *,
    file: str | None = None,
    routes: list[dict[str, Any]] | None = None,
    content: str | None = None,
    diff: str | None = None,
    request_schema: dict | None = None,
    response_schema: dict | None = None,
) -> dict[tuple[str, str], dict]:
    """Normalize every input shape into {(METHOD, route): incoming contract}."""
    incoming: dict[tuple[str, str], dict] = {}

    def add(route: str, method: str, source_file: str | None, req: dict | None, resp: dict | None) -> None:
        if not route or not method:
            return
        key = (method.upper(), route)
        incoming.setdefault(
            key,
            {
                "route": route,
                "method": method.upper(),
                "file": source_file,
                "request_schema": req,
                "response_schema": resp,
            },
        )

    for hint in routes or []:
        if not isinstance(hint, dict):
            continue
        add(
            str(hint.get("route") or ""),
            str(hint.get("method") or ""),
            hint.get("file") or file,
            hint.get("request_schema") if hint.get("request_schema") is not None else request_schema,
            hint.get("response_schema") if hint.get("response_schema") is not None else response_schema,
        )

    if content:
        text = content[:MAX_CONTENT_CHARS]
        for r in diff_parser.extract_routes_from_text(text, file or "<content>"):
            add(r["route"], r["method"], r.get("file"), request_schema, response_schema)
    if diff:
        text = diff[:MAX_DIFF_CHARS]
        for r in diff_parser.parse_diff(text).routes_added:
            add(r["route"], r["method"], r.get("file"), request_schema, response_schema)

    return incoming


def registered_contracts(db: Session, project_id: uuid.UUID) -> list[dict]:
    return [
        {
            "route": r.route,
            "method": r.method,
            "request_schema": r.request_schema,
            "response_schema": r.response_schema,
        }
        for r in db.scalars(select(ApiContract).where(ApiContract.project_id == project_id)).all()
    ]


def run_check(db: Session, project_id: uuid.UUID, incoming: dict[tuple[str, str], dict]) -> dict:
    """The check payload — the exact shape both callers return."""
    registered = registered_contracts(db, project_id)
    findings = conflict_service.detect_contract_conflicts(list(incoming.values()), registered)

    # Exact method+route hits among registered contracts. The shape-conflict
    # detector is deliberately conservative when a side declares no schema;
    # these matches let the client show/pin the REGISTERED contract even in the
    # "already registered, shape not comparable" case (never a false conflict).
    matches: list[dict] = []
    for inc in incoming.values():
        inc_path = conflict_service.normalize_route(inc["route"])
        for reg in registered:
            if inc_path != conflict_service.normalize_route(reg["route"]):
                continue
            if inc["method"].upper() != (reg.get("method") or "").upper():
                continue
            matches.append(
                {
                    "incoming": {"route": inc["route"], "method": inc["method"], "file": inc.get("file")},
                    "registered": {
                        "route": reg["route"],
                        "method": reg["method"],
                        "request_schema": reg.get("request_schema"),
                        "response_schema": reg.get("response_schema"),
                    },
                }
            )

    return {
        "project_id": str(project_id),
        "checked": len(incoming),
        "incoming": [
            {"route": v["route"], "method": v["method"], "file": v.get("file")} for v in incoming.values()
        ],
        "conflicts": findings,
        "registered_matches": matches,
        "clean": not findings,
        # "conflict" = the write should be stopped/confirmed; "warning" = advisory.
        "has_blocking": any(f.get("severity") == "conflict" for f in findings),
    }

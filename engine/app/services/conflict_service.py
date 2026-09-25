"""Deterministic API-contract conflict detection — Day 4 Part B (build plan).

Repo rule #3: detection is plain Python — no LLM, no DB, no I/O. Pure functions
over plain dicts so they are trivially unit-testable.

Two ways a contract enters the system, both funnel through here:
  - POST /projects/:id/contracts            (plugin "after" hook, dashboard)
  - POST /projects/:id/github-webhook       (push → parse_diff() → contracts)

What we flag (severity order):
  - conflicting_shape  (conflict)  same method+route already registered, incoming
                                   request/response schema diverges from it — the
                                   "two agents invent different API shapes" case.
  - method_divergence  (conflict)  same normalized path (params like :id or {id}
                                   equalized), different HTTP method — e.g. one
                                   agent builds POST /api/auth/session, another
                                   invents GET /api/auth/session.
  - sibling_collision  (warning)   new route under an existing feature prefix
                                   (same first two segments) with a different
                                   shape — likely reinventing the same intent.
  - duplicate          (info)      same method+route+shape — normal re-report,
                                   explicitly NOT a conflict; no action needed.

A finding dict looks like:
  {kind, severity, route, method, existing: {...}, incoming: {...}}

Determinism guarantee: identical inputs produce identical outputs, every time.
"""

from __future__ import annotations

import re

_SEVERITY_ORDER = {"conflict": 0, "warning": 1, "info": 2}


def _norm_schema(schema: dict | None) -> dict:
    """Normalize a schema dict for comparison: None -> {}, sorted keys, str values.

    Keeps the comparison deterministic even when sources serialize JSON with
    different key orders or types ({"page": "1"} vs {"page": 1}).
    """
    if not schema:
        return {}
    return {str(k): _norm_value(v) for k, v in sorted(schema.items())}


def _norm_value(v) -> object:
    if isinstance(v, dict):
        return {str(k): _norm_value(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_norm_value(x) for x in v]
    if v is None:
        return "null"
    return str(v)


def _schema_diff(a: dict, b: dict) -> list[str]:
    """Human-readable list of fields that differ between two (normalized) schemas."""
    diffs = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return [k if k else "(empty)" for k in diffs]


def _normalize_path(route: str) -> str:
    """Lowercase, strip slashes, and equalize path-param styles (:id == {id} == <id>)."""
    path = route.strip().lower().strip("/")
    path = re.sub(r"[:{<]([a-z_][a-z0-9_]*)[}>]?", r"<\1>", path)
    return path


def _prefix(route: str, segments: int = 2) -> str:
    """First N path segments — the 'feature area' (e.g. /api/auth)."""
    parts = _normalize_path(route).split("/")
    return "/".join(parts[:segments])


def _contract_view(c: dict) -> dict:
    """Pick the fields that matter for a finding, so event payloads stay small."""
    return {
        "route": c.get("route", ""),
        "method": (c.get("method") or "").upper(),
        "request_schema": _norm_schema(c.get("request_schema")),
        "response_schema": _norm_schema(c.get("response_schema")),
    }


def _finding(kind: str, existing: dict, incoming: dict, **extra) -> dict:
    f = {
        "kind": kind,
        "severity": "conflict" if kind in ("conflicting_shape", "method_divergence") else ("warning" if kind == "sibling_collision" else "info"),
        "existing": _contract_view(existing),
        "incoming": _contract_view(incoming),
    }
    f.update(extra)
    return f


def _has_any_schema(c: dict) -> bool:
    """True when the contract declares at least one request/response field."""
    return bool(_norm_schema(c.get("request_schema")) or _norm_schema(c.get("response_schema")))


def _shapes_differ(a: dict, b: dict) -> bool:
    """True when the two contracts declare any different request/response field.

    Deliberately strict: any divergence in declared fields — including one side
    adding a field the other doesn't have — counts. Deterministic, easy to
    explain in the demo, and matches the master-doc definition of a semantic
    conflict (two agents inventing different shapes for the same endpoint).

    If EITHER side declares no schema fields at all (e.g. a webhook-registered
    contract, whose diff only proves path+method), the shape is not comparable
    and we refuse to claim a shape conflict — only method/prefix rules apply.
    """
    if not _has_any_schema(a) or not _has_any_schema(b):
        return False
    ra, rb = _norm_schema(a.get("request_schema")), _norm_schema(b.get("request_schema"))
    pa, pb = _norm_schema(a.get("response_schema")), _norm_schema(b.get("response_schema"))
    for x, y in ((ra, rb), (pa, pb)):
        # any key present in one and absent-or-different in the other
        if any(x.get(k) != y.get(k) for k in set(x) | set(y)):
            return True
    return False


def detect_contract_conflicts(incoming: list[dict], existing: list[dict]) -> list[dict]:
    """Compare freshly-reported contracts against the project's registered ones.

    Args:
        incoming: new contracts (dicts with route, method, request_schema, response_schema)
        existing: contracts already registered for the project

    Returns findings sorted by severity (conflict first), then by route/method
    so output order is stable. Highest-severity finding wins when an
    (incoming, existing) pair matches multiple rules — one pair, one finding.
    """
    findings: list[dict] = []

    for inc in incoming:
        inc_method = (inc.get("method") or "").upper()
        inc_path = _normalize_path(inc.get("route", ""))
        inc_prefix = _prefix(inc.get("route", ""))
        inc_view = _contract_view(inc)
        matched_path = False

        for ex in existing:
            ex_method = (ex.get("method") or "").upper()
            ex_path = _normalize_path(ex.get("route", ""))

            # --- exact same method + route ------------------------------
            if inc_path == ex_path and inc_method == ex_method:
                matched_path = True
                if _shapes_differ(inc, ex):
                    diff = sorted(
                        set(
                            _schema_diff(_norm_schema(inc.get("request_schema")), _norm_schema(ex.get("request_schema")))
                            + _schema_diff(_norm_schema(inc.get("response_schema")), _norm_schema(ex.get("response_schema")))
                        )
                    )
                    findings.append(
                        _finding("conflicting_shape", ex, inc, differing_fields=diff)
                    )
                else:
                    findings.append(_finding("duplicate", ex, inc))
                continue

            # --- same path, different method -----------------------------
            if inc_path == ex_path and inc_method != ex_method:
                matched_path = True
                findings.append(_finding("method_divergence", ex, inc))
                continue

            # --- same feature prefix, different path shape ---------------
            if inc_prefix and inc_prefix == _prefix(ex.get("route", "")) and inc_path != ex_path:
                matched_path = True
                findings.append(_finding("sibling_collision", ex, inc))

        # a route matching no existing contract at all is clean — no finding

    # one finding per (incoming, existing) pair, highest severity wins
    deduped: dict[tuple, dict] = {}
    for f in findings:
        key = (
            f["incoming"]["route"],
            f["incoming"]["method"],
            f["existing"]["route"],
            f["existing"]["method"],
            f["kind"],
        )
        prev = deduped.get(key)
        if prev is None or _SEVERITY_ORDER.get(f["severity"], 9) < _SEVERITY_ORDER.get(prev["severity"], 9):
            deduped[key] = f

    ranked = sorted(
        deduped.values(),
        key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), f["incoming"]["route"], f["incoming"]["method"]),
    )
    # keep only findings that require attention; pure duplicates are info-level
    return [f for f in ranked if f["severity"] in ("conflict", "warning")]

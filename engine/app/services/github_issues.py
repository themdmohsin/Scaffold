"""Auto-GitHub-issue action for flagged conflicts — Day 4 Part B (build plan).

When conflict detection flags a conflict, this module POSTs a GitHub issue so
a human sees it outside the Scaffold UI. Build plan Day 4 Person B, second
bullet: "Wire the auto-GitHub-issue action for when a conflict is flagged
(POST to GitHub's issue API)."

Environment (exact names frozen in docs/API_CONTRACTS.md + .env.example):
    GITHUB_TOKEN         PAT with repo scope; without it, no issues are created
                         (fail-open — the event + blocker are recorded regardless).
    SCAFFOLD_GITHUB_REPO "owner/repo" to file issues against. Day-4 default:
                         themdmohsin/Scaffold (overridable via env).

Fail-open rule: ANY failure here (no token, bad repo, GitHub down, rate limit,
label creation) must never break ingestion. The engine already wrote the
conflict_flagged event + blockers row by the time this runs.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json

import httpx

from app.config import settings

_GITHUB_API = "https://api.github.com"
_LABEL = "scaffold-conflict"


def _repo_slug(explicit: str | None) -> str:
    return (explicit or "").strip().strip("/") or "themdmohsin/Scaffold"


def _issue_title(finding: dict) -> str:
    inc = finding.get("incoming", {})
    return f"[SCAFFOLD] Conflicting API contract: {inc.get('method', '?')} {inc.get('route', '?')}"


def _issue_body(finding: dict, context: dict) -> str:
    """Deterministic markdown body — deterministic facts only (repo rule #2/#3)."""
    inc, ex = finding.get("incoming", {}), finding.get("existing", {})
    lines = [
        "## Semantic API conflict detected by Scaffold",
        "",
        f"**Kind:** `{finding.get('kind', 'unknown')}` (severity: `{finding.get('severity', '?')}`)",
        f"**Detected:** {_dt.datetime.now(_dt.timezone.utc).isoformat()}",
        "",
        "### Incoming contract (newly reported)",
        f"- Route: `{inc.get('route', '?')}`",
        f"- Method: `{inc.get('method', '?')}`",
        f"- Request schema: `{json.dumps(inc.get('request_schema') or {}, sort_keys=True)}`",
        f"- Response schema: `{json.dumps(inc.get('response_schema') or {}, sort_keys=True)}`",
        f"- Source: {context.get('incoming_source', 'plugin report_change')}",
        "",
        "### Already-registered contract",
        f"- Route: `{ex.get('route', '?')}`",
        f"- Method: `{ex.get('method', '?')}`",
        f"- Request schema: `{json.dumps(ex.get('request_schema') or {}, sort_keys=True)}`",
        f"- Response schema: `{json.dumps(ex.get('response_schema') or {}, sort_keys=True)}`",
        f"- Source: {context.get('existing_source', 'registered earlier')}",
        "",
    ]
    if finding.get("differing_fields"):
        lines += ["### Differing fields", *[f"- `{k}`" for k in finding["differing_fields"]], ""]
    lines += [
        "---",
        "Filed automatically by the Scaffold engine. See the project dashboard for the full shared context.",
    ]
    return "\n".join(lines)


async def _ensure_label(client: httpx.AsyncClient, headers: dict, repo: str) -> None:
    """Create the scaffold-conflict label once; 422 (already exists) is fine."""
    try:
        await client.post(
            f"{_GITHUB_API}/repos/{repo}/labels",
            headers=headers,
            json={"name": _LABEL, "color": "D93F0B", "description": "Semantic API conflict flagged by Scaffold"},
        )
    except httpx.HTTPError:
        pass  # label is cosmetic — never block issue creation on it


async def _find_open_issue(client: httpx.AsyncClient, headers: dict, repo: str, title: str) -> bool:
    """True if an OPEN issue with the exact same title already exists (dedupe)."""
    try:
        res = await client.get(
            f"{_GITHUB_API}/repos/{repo}/issues",
            headers=headers,
            params={"state": "open", "per_page": 100},
        )
        res.raise_for_status()
        for issue in res.json():
            if issue.get("title") == title and "pull_request" not in issue:
                return True
        return False
    except httpx.HTTPError:
        return False  # can't check -> don't dedupe (fail open to creating)


async def create_conflict_issue(
    finding: dict,
    context: dict | None = None,
    repo: str | None = None,
    token: str | None = None,
) -> dict | None:
    """File one GitHub issue for a conflict finding. Returns the created issue dict,
    a dict with {'skipped': reason}, or None when nothing was attempted.

    Never raises — callers treat this as best-effort by design.
    """
    tok = token or settings.github_token
    if not tok:
        return {"skipped": "no GITHUB_TOKEN configured"}
    target = _repo_slug(repo)

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {tok}",
    }
    title = _issue_title(finding)
    body = _issue_body(finding, context or {})

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await _ensure_label(client, headers, target)
            if await _find_open_issue(client, headers, target, title):
                return {"skipped": "identical open issue already exists"}
            res = await client.post(
                f"{_GITHUB_API}/repos/{target}/issues",
                headers=headers,
                json={"title": title, "body": body, "labels": [_LABEL]},
            )
            if res.status_code in (401, 403, 404, 410, 422):
                return {"skipped": f"github rejected issue creation: HTTP {res.status_code}"}
            res.raise_for_status()
            data = res.json()
            return {"number": data.get("number"), "url": data.get("html_url"), "title": data.get("title")}
    except httpx.HTTPError as exc:
        return {"skipped": f"github unreachable: {exc}"[:200]}
    except Exception as exc:  # truly unexpected — still never propagate
        return {"skipped": f"unexpected error: {exc}"[:200]}


def spawn_issue_creation(finding: dict, context: dict | None = None, repo: str | None = None) -> None:
    """Fire-and-forget wrapper: schedule issue creation without blocking ingestion.

    Safe to call from sync contexts (route handlers) — grabs or creates a loop.
    All results/exceptions land in this module, never in the caller.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(create_conflict_issue(finding, context, repo))
    except RuntimeError:
        # no running loop (e.g. called from a sync route) — run in a background thread
        import threading

        def _run() -> None:
            asyncio.run(create_conflict_issue(finding, context, repo))

        threading.Thread(target=_run, daemon=True, name="scaffold-issue-creator").start()

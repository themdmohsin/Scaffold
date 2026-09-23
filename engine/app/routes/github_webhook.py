"""GitHub webhook receiver: POST /projects/:id/github-webhook (frozen contract).

Push events only, for now. Flow (master doc §6):
  verify HMAC → fetch commit diffs from GitHub REST → parse DIFF (deterministic)
  → write commits + api_contracts + events.

The diff is the source of truth. The LLM one-liner summary (commits.summary)
lands Day 3 with reasoning.py; the column stays null until then.
"""

import hashlib
import hmac
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ApiContract, Commit, Event, Project
from app.db.session import get_db
from app.services.diff_parser import parse_diff

router = APIRouter(tags=["webhook"])

_GITHUB_API = "https://api.github.com"


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """HMAC-SHA256 of the raw body must match X-Hub-Signature-256."""
    secret = settings.github_webhook_secret
    if not secret:
        # Fail closed: without a configured secret we cannot trust the payload.
        raise HTTPException(status_code=503, detail="GITHUB_WEBHOOK_SECRET is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=400, detail="missing X-Hub-Signature-256 header")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.removeprefix("sha256=")):
        raise HTTPException(status_code=403, detail="invalid signature")


async def _fetch_commit_diff(sha: str, repo_full_name: str) -> tuple[list[str], str, str, str]:
    """Fetch one commit from the GitHub REST API.

    Returns (files_changed, message, author, concatenated patch text).
    The REST `files[].patch` IS the unified diff — parse it deterministically.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(f"{_GITHUB_API}/repos/{repo_full_name}/commits/{sha}", headers=headers)
        if res.status_code == 404:
            raise HTTPException(status_code=422, detail=f"commit {sha} not found in {repo_full_name}")
        if res.status_code == 403:
            raise HTTPException(status_code=422, detail="GitHub API rate limit or bad GITHUB_TOKEN")
        res.raise_for_status()
        data = res.json()

    files = data.get("files") or []
    # GitHub's files[].patch contains ONLY hunks (no `diff --git` headers), so
    # synthesize standard unified-diff headers per file — the parser needs the
    # file path to classify routes/deps/env keys deterministically.
    parts = []
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue
        name = f["filename"]
        parts.append(f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n{patch}")
    patch_text = "\n".join(parts)
    author = (data.get("commit", {}).get("author") or {}).get("name", "")
    return (
        [f["filename"] for f in files],
        data.get("commit", {}).get("message", ""),
        author,
        patch_text,
    )


@router.post("/projects/{project_id}/github-webhook")
async def github_webhook(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
) -> dict:
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid project id") from None
    if not db.get(Project, pid):
        raise HTTPException(status_code=404, detail="project not found")

    raw = await request.body()
    _verify_signature(raw, x_hub_signature_256)

    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event != "push":
        return {"ok": True, "ignored": x_github_event}

    payload = json.loads(raw)
    repo = (payload.get("repository") or {}).get("full_name")
    if not repo:
        raise HTTPException(status_code=400, detail="push payload missing repository.full_name")

    results = []
    for c in payload.get("commits", []):
        sha = c.get("id")
        if not sha:
            continue
        try:
            files, message, author, patch_text = await _fetch_commit_diff(sha, repo)
        except HTTPException:
            raise
        except Exception as exc:  # network/GitHub outage: report per-commit, keep going
            results.append({"sha": sha[:10], "error": str(exc)[:200]})
            continue

        summary = parse_diff(patch_text)  # deterministic — repo rule #2

        db.add(
            Commit(
                project_id=pid,
                sha=sha,
                message=message,
                author=author,
                files_changed=files,
                summary=None,  # Day 3: LLM one-liner via reasoning.py
            )
        )
        for route_info in summary.routes_added:
            db.add(
                ApiContract(
                    project_id=pid,
                    route=route_info["route"],
                    method=route_info["method"],
                    request_schema=None,
                    response_schema=None,
                )
            )
        db.add(
            Event(
                project_id=pid,
                type="commit_ingested",
                payload={
                    "sha": sha[:10],
                    "files_changed": len(files),
                    "routes_added": summary.routes_added,
                    "dependencies_added": summary.dependencies_added,
                    "env_keys_added": summary.env_keys_added,
                },
            )
        )
        results.append(
            {
                "sha": sha[:10],
                "files": len(files),
                "routes": len(summary.routes_added),
                "deps": len(summary.dependencies_added),
                "env_keys": len(summary.env_keys_added),
            }
        )

    db.commit()
    return {"ok": True, "processed": results}

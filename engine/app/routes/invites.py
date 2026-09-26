"""Day 5 Person A: teammate invites — the shareable project-join link.

POST /projects/{id}/invite -> a signed, expiring join code (stateless).
POST /projects/join        -> redeems a code and creates the project's users row.

Why stateless: the Day-1 schema is frozen (docs/SCHEMA.md has no invites table)
and Day 5 adds no tables. The code is `project_id.expiry_epoch.signature` —
HMAC-SHA256 under GITHUB_WEBHOOK_SECRET (already required for the webhook),
truncated for shareability. Anyone holding a valid unexpired code can join the
project — treat the link like the secret it is. No new env vars.
"""

import hashlib
import hmac
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Event, Project, User
from app.db.session import get_db

router = APIRouter(tags=["invites"])

CODE_TTL_SECONDS = 7 * 24 * 3600  # one week: long enough for a hackathon demo


def _signing_key() -> bytes:
    secret = (settings.github_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="invite flow unavailable: GITHUB_WEBHOOK_SECRET is not set",
        )
    return secret.encode()


def _mac(project_id: str, expiry: str) -> str:
    return hmac.new(
        _signing_key(), f"invite:{project_id}:{expiry}".encode(), hashlib.sha256
    ).hexdigest()[:20]


def make_code(project_id: uuid.UUID, now: float | None = None) -> str:
    """Pure (except for the clock): `project_id.expiry_epoch.signature`."""
    expiry = str(int((now if now is not None else time.time()) + CODE_TTL_SECONDS))
    return f"{project_id}.{expiry}.{_mac(str(project_id), expiry)}"


def parse_code(code: str, now: float | None = None) -> uuid.UUID:
    """Validate signature + expiry and return the project id. Raises HTTP 400."""
    parts = (code or "").strip().split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="malformed invite code")
    project_id, expiry, sig = parts
    if not project_id or not expiry.isdigit() or not sig:
        raise HTTPException(status_code=400, detail="malformed invite code")
    if not hmac.compare_digest(_mac(project_id, expiry), sig):
        raise HTTPException(status_code=400, detail="invite code signature invalid")
    if float(now if now is not None else time.time()) > int(expiry):
        raise HTTPException(status_code=400, detail="invite code expired")
    try:
        return uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed invite code") from exc


class JoinBody(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=80)

    @field_validator("role")
    @classmethod
    def _collapse_role_whitespace(cls, v: str | None) -> str | None:
        """Same trust boundary as the name: a role is interpolated into the LLM
        TEAM ROSTER line, so a newline/tab in it would forge a prompt line.
        whitespace-collapsed or None — never other shapes."""
        return _sanitize_label(v) if v else v


def _sanitize_label(raw: str) -> str:
    """Collapse all whitespace runs to single spaces.

    A newline or tab inside a name would become a forged line inside the LLM
    TEAM ROSTER block (render_roster_block emits one line per user) — an
    injection vector straight into every agent's prompt.
    """
    return re.sub(r"\s+", " ", raw).strip()


@router.post("/projects/{project_id}/invite", status_code=201)
def create_invite(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="project not found")
    code = make_code(project_id)
    return {
        "invite_url": f"/projects/join?code={code}",
        "code": code,
        "expires_at_epoch": int(code.split(".")[1]),
    }


@router.post("/projects/join", status_code=201)  # 201 = created; existing returns 200 below
def join_project(body: JoinBody, db: Session = Depends(get_db), response: Response = None) -> dict:
    project_id = parse_code(body.code)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    name = _sanitize_label(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="name must not be blank")
    existing = db.scalars(
        select(User).where(User.project_id == project_id, func.lower(User.name) == name.lower())
    ).first()
    if existing:
        # One shared link, many redemptions: a repeat join returns the SAME user
        # instead of planting duplicate rows that would poison the roster.
        response.status_code = 200
        return {
            "user_id": str(existing.id),
            "project_id": str(project_id),
            "name": existing.name,
            "role": existing.role,
            "existing": True,
        }

    user = User(project_id=project_id, name=name, role=body.role)
    db.add(user)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            type="teammate_joined",
            payload={"user_id": str(user.id), "name": user.name},
        )
    )
    db.commit()
    db.refresh(user)
    return {
        "user_id": str(user.id),
        "project_id": str(project_id),
        "name": user.name,
        "role": user.role,
    }

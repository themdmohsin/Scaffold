"""Invites — Phase 6 (real authentication): a REAL invites table.

Replaces the stateless Day-5 signed code with persisted, governable invite
rows (requirement 4): single/multi-use caps, expiry, role pre-assignment,
revocation, and a redemption trail showing who joined. The ENDPOINTS and
response shapes stay backward compatible:

    POST /projects/{id}/invite  → 201 {invite_url, code, expires_at_epoch}
    POST /projects/join         → 201 new | 200 existing  {user_id, project_id, name, role}

What changed underneath:
  • create: an `invites` row is written (code format unchanged:
    `project_id.expiry.sig`, so OLD links still validate; the DB row now
    governs revocation/uses). Owner/admin only — was: anyone with the URL.
  • join: REQUIRES a signed-in caller (Supabase JWT) — the invite binds the
    CALLER's account to the project, creating accounts-backed roster +
    membership rows. The legacy `name`/`role` body fields are accepted but
    ignored when the caller is authenticated (identity comes from the token,
    requirement 1). Unauthenticated joins answer 401.
  • the code's HMAC signature + expiry are still verified first (defense in
    depth; the DB row adds governance on top).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Account,
    Event,
    Invite,
    InviteRedemption,
    Project,
    ProjectMember,
    User,
)
from app.db.session import get_db
from app.services import auth as auth_service
from app.services.auth import Principal, require_principal, require_role

router = APIRouter(tags=["invites"])

CODE_TTL_SECONDS = 7 * 24 * 3600  # one week: long enough for a hackathon demo


# ---------------------------------------------------------------------------
# The code format is UNCHANGED from Day 5 (old links keep working)
# ---------------------------------------------------------------------------


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


def make_code(project_id: uuid.UUID, now: float | None = None, ttl_seconds: int = CODE_TTL_SECONDS) -> str:
    """Pure (except for the clock): `project_id.expiry_epoch.signature`."""
    expiry = str(int((now if now is not None else time.time()) + ttl_seconds))
    return f"{project_id}.{expiry}.{_mac(str(project_id), expiry)}"


def parse_code(code: str, now: float | None = None) -> tuple[uuid.UUID, int]:
    """Validate signature + expiry; return (project_id, expiry_epoch). Raises 400."""
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
        return uuid.UUID(project_id), int(expiry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed invite code") from exc


def _sanitize_label(raw: str) -> str:
    """Collapse all whitespace runs to single spaces.

    A newline or tab inside a name would become a forged line inside the LLM
    TEAM ROSTER block (render_roster_block emits one line per user) — an
    injection vector straight into every agent's prompt.
    """
    return re.sub(r"\s+", " ", raw).strip()


class InviteCreate(BaseModel):
    # Legacy compat: accepted, ignored — identity comes from the principal.
    requesting_user_id: uuid.UUID | None = None
    # Additive governance knobs (defaults preserve the Day-5 behavior).
    supabase_role: str = Field(default="member", pattern="^(owner|admin|member)$")
    max_uses: int | None = Field(default=None, ge=1)
    ttl_seconds: int | None = Field(default=None, ge=60, le=90 * 24 * 3600)


@router.post("/projects/{project_id}/invite", status_code=201)
def create_invite(
    project_id: uuid.UUID,
    body: InviteCreate | None = None,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    response: Response = None,
) -> dict:
    """Create (or return the live) invite for a project — owner/admin gated."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    member = require_role(db, project_id, principal, "admin")

    body = body or InviteCreate()
    ttl = body.ttl_seconds or CODE_TTL_SECONDS

    # Reuse the newest still-valid invite unless the caller asks for new
    # constraints — the Day-5 "one link per project" behavior, now governed.
    existing = (
        db.scalars(
            select(Invite)
            .where(
                Invite.project_id == project_id,
                Invite.revoked_at.is_(None),
            )
            .order_by(Invite.created_at.desc())
        )
        .unique()
        .first()
    )
    if (
        existing
        and body.max_uses is None
        and body.ttl_seconds is None
        and body.supabase_role == "member"
        and (existing.expires_at is None or existing.expires_at > datetime.now(timezone.utc))
        and (existing.max_uses is None or existing.use_count < existing.max_uses)
        and existing.supabase_role == "member"
    ):
        invite = existing
        code = invite.code
    else:
        # The code is content-defined (`project_id.expiry.signature`), so two
        # invites created for the same project in the same second with the same
        # TTL produce the SAME code — and a revoked row from a moment earlier
        # still occupies that code on the UNIQUE(code) index. Bump the expiry
        # until the code is free (normally zero or one iteration).
        ttl_used = ttl
        for _ in range(10):
            code = make_code(project_id, ttl_seconds=ttl_used)
            if db.scalar(select(Invite.id).where(Invite.code == code)) is None:
                break
            ttl_used += 1
        else:
            raise HTTPException(status_code=409, detail="could not allocate a unique invite code; retry")
        invite = Invite(
            project_id=project_id,
            code=code,
            invited_by=principal.account_id,
            supabase_role=body.supabase_role,
            max_uses=body.max_uses,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_used),
        )
        db.add(invite)
        db.flush()
        db.add(
            Event(
                project_id=project_id,
                type="invite_created",
                payload={
                    "invite_id": str(invite.id),
                    "created_by_account_id": str(principal.account_id),
                    "supabase_role": invite.supabase_role,
                    "max_uses": invite.max_uses,
                },
            )
        )
        db.commit()

    return {
        "invite_id": str(invite.id),
        "invite_url": f"/projects/join?code={code}",
        "code": code,
        "expires_at_epoch": int(code.split(".")[1]),
        "supabase_role": invite.supabase_role,
        "max_uses": invite.max_uses,
        "use_count": invite.use_count,
        "revoked": False,
    }


class InviteOut(BaseModel):
    id: str
    code: str
    supabase_role: str
    max_uses: int | None
    use_count: int
    expires_at: str | None
    revoked: bool
    created_at: str
    invited_by: str | None
    redemptions: list[dict] = Field(default_factory=list)


@router.get("/projects/{project_id}/invites")
def list_invites(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    """All invites for the project + who redeemed them (owner/admin only)."""
    require_role(db, project_id, principal, "admin")
    rows = (
        db.scalars(
            select(Invite)
            .where(Invite.project_id == project_id)
            .order_by(Invite.created_at.desc())
        )
        .unique()
        .all()
    )
    out = []
    for inv in rows:
        redemptions = db.scalars(
            select(InviteRedemption).where(InviteRedemption.invite_id == inv.id)
        ).unique().all()
        accounts = {
            a.id: a
            for a in db.scalars(
                select(Account).where(Account.id.in_([r.account_id for r in redemptions] or [uuid.uuid4()]))
            ).unique().all()
        }
        out.append(
            {
                "id": str(inv.id),
                "code": inv.code,
                "supabase_role": inv.supabase_role,
                "max_uses": inv.max_uses,
                "use_count": inv.use_count,
                "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
                "revoked": inv.revoked_at is not None,
                "created_at": inv.created_at.isoformat(),
                "invited_by": str(inv.invited_by) if inv.invited_by else None,
                "redemptions": [
                    {
                        "account_id": str(r.account_id),
                        "email": accounts.get(r.account_id).email if accounts.get(r.account_id) else None,
                        "name": accounts.get(r.account_id).full_name if accounts.get(r.account_id) else None,
                        "redeemed_at": r.redeemed_at.isoformat(),
                    }
                    for r in redemptions
                ],
            }
        )
    return out


@router.delete("/projects/{project_id}/invites/{invite_id}")
def revoke_invite(
    project_id: uuid.UUID,
    invite_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Revoke an invite immediately: further redemptions fail (req. 4)."""
    require_role(db, project_id, principal, "admin")
    inv = db.scalar(
        select(Invite).where(Invite.id == invite_id, Invite.project_id == project_id)
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invite not found")
    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(timezone.utc)
        db.add(
            Event(
                project_id=project_id,
                type="invite_revoked",
                payload={"invite_id": str(inv.id), "revoked_by_account_id": str(principal.account_id)},
            )
        )
        db.commit()
    return {"revoked": True, "id": str(inv.id)}


# ---------------------------------------------------------------------------
# Join — redemption with a SIGNED-IN caller
# ---------------------------------------------------------------------------


class JoinBody(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=80)  # legacy: accepted, ignored when signed in
    role: str | None = Field(default=None, max_length=80)  # legacy: accepted, ignored when signed in

    @field_validator("role")
    @classmethod
    def _collapse_role_whitespace(cls, v: str | None) -> str | None:
        """Same trust boundary as the name: a role is interpolated into the LLM
        TEAM ROSTER line, so a newline/tab in it would forge a prompt line."""
        return _sanitize_label(v) if v else v


@router.post("/projects/join", status_code=201)  # 201 = created; existing returns 200 below
def join_project(
    body: JoinBody,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    response: Response = None,
) -> dict:
    """Redeem an invite code AS THE CALLER (verified JWT). The code selects the
    project; the TOKEN selects the identity — never the other way around."""
    project_id, _expiry = parse_code(body.code)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    if principal.via != "jwt":
        raise HTTPException(
            status_code=403,
            detail="joining requires a signed-in Supabase session (PATs cannot accept invites)",
        )

    # Look the code up EXACTLY (never "the newest live invite"): a revoked row
    # must be found and refused, otherwise revocation would be bypassed by
    # minting a fresh row for the same code. Governance checks follow.
    invite = db.scalar(
        select(Invite).where(
            Invite.project_id == project_id,
            Invite.code == body.code.strip(),
        )
    )
    # Legacy-code path: a still-valid Day-5 HMAC code with no row at all (a link
    # created before auth existed) is minted a row on first post-auth use so it
    # becomes governed like every other invite.
    if invite is None:
        invite = Invite(
            project_id=project_id,
            code=body.code.strip(),
            supabase_role="member",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
        )
        db.add(invite)
        db.flush()

    if invite.revoked_at is not None:
        raise HTTPException(status_code=403, detail="this invite has been revoked")
    if invite.expires_at is not None and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="invite code expired")
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        raise HTTPException(status_code=403, detail="this invite has no uses left")

    existing_member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.account_id == principal.account_id,
        )
    )
    if existing_member and existing_member.status == "active":
        user = auth_service.ensure_roster_user(db, project_id, principal)
        response.status_code = 200
        return {
            "user_id": str(user.id),
            "project_id": str(project_id),
            "name": user.name,
            "role": user.role,
            "existing": True,
        }

    # New membership at the invite's pre-assigned role.
    member = auth_service.set_project_role(
        db, project_id, principal.account_id, invite.supabase_role
    )
    member.invite_id = invite.id
    member.invited_by = invite.invited_by
    invite.use_count += 1

    user = auth_service.ensure_roster_user(db, project_id, principal)
    db.add(
        InviteRedemption(
            invite_id=invite.id,
            account_id=principal.account_id,
            roster_user_id=user.id,
        )
    )
    db.add(
        Event(
            project_id=project_id,
            type="teammate_joined",
            payload={
                "user_id": str(user.id),
                "name": user.name,
                "account_id": str(principal.account_id),
                "via": "invite",
            },
        )
    )
    db.commit()
    db.refresh(user)
    return {
        "user_id": str(user.id),
        "project_id": str(project_id),
        "name": user.name,
        "role": user.role,
        "supabase_role": member.supabase_role,
    }

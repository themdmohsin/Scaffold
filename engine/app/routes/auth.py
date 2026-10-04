"""Auth routes — Phase 6 (real authentication), all additive.

    GET    /auth/me                 → the verified principal
    POST   /auth/tokens             → mint a personal access token (shown ONCE)
    GET    /auth/tokens             → list the caller's tokens (metadata only)
    DELETE /auth/tokens/{token_id}  → revoke
    GET    /projects                → projects the caller is a member of (req. 6)
    POST   /projects                → creates a project OWNED BY THE CALLER (req. 6)
    POST   /projects/join           → live in routes/invites.py (invite redemption)

Supabase Auth itself (signup/login/OAuth) is NOT proxied here: the dashboard
talks to Supabase directly (supabase-js) and sends the resulting JWT to the
engine. This module exposes the engine-side identity surface only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, PersonalAccessToken, Project, ProjectMember
from app.db.session import get_db
from app.services import auth as auth_service
from app.services.auth import Principal, require_principal

router = APIRouter(tags=["auth"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@router.get("/auth/me")
def whoami(principal: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> dict:
    """Echo the VERIFIED identity (never trusted from a body). Includes the
    caller's project memberships so a client can route without guessing."""
    account = db.get(Account, principal.account_id)
    memberships = db.scalars(
        select(ProjectMember).where(
            ProjectMember.account_id == principal.account_id,
            ProjectMember.status == "active",
        )
    ).all()
    return {
        "account_id": str(principal.account_id),
        "via": principal.via,
        "email": account.email if account else principal.email,
        "full_name": account.full_name if account else None,
        "auth_provider": account.auth_provider if account else None,
        "token_scoped_project_id": (
            str(principal.token_scoped_project_id) if principal.token_scoped_project_id else None
        ),
        "memberships": [
            {
                "project_id": str(m.project_id),
                "supabase_role": m.supabase_role,
                "joined_at": _iso(m.joined_at),
            }
            for m in memberships
        ],
    }


# ---------------------------------------------------------------------------
# Personal access tokens (machine credentials for plugin / CLI / MCP)
# ---------------------------------------------------------------------------


class TokenCreate(BaseModel):
    name: str = Field(default="token", min_length=1, max_length=80)
    # Optional: pin the token to ONE project (the plugin/CI use case). The
    # token then cannot act on any other project (enforced in require_member).
    project_id: uuid.UUID | None = None
    # Optional per-token scope hints for future narrowing; the engine's role
    # model remains the enforcement layer — scopes never WIDEN it.
    scopes: list[str] = Field(default_factory=list)


@router.post("/auth/tokens", status_code=201)
def create_token(
    body: TokenCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Mint a PAT for the CALLER's own account. The raw token is returned
    exactly once and only its SHA-256 hash is stored (requirement 3)."""
    if principal.via == "pat":
        raise HTTPException(
            status_code=403,
            detail="a personal access token cannot mint further tokens — sign in with Supabase Auth",
        )
    if body.project_id and not db.get(Project, body.project_id):
        raise HTTPException(status_code=404, detail="project not found")

    raw = auth_service.new_token_raw()
    row = PersonalAccessToken(
        account_id=principal.account_id,
        name=body.name.strip() or "token",
        token_hash=auth_service.hash_token(raw),
        token_prefix=raw[:15] + "…",
        project_id=body.project_id,
        scopes={"scopes": body.scopes} if body.scopes else {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": str(row.id),
        "token": raw,  # THE ONLY RESPONSE THAT EVER CARRIES THE RAW TOKEN
        "name": row.name,
        "token_prefix": row.token_prefix,
        "project_id": str(row.project_id) if row.project_id else None,
        "created_at": _iso(row.created_at),
        "warning": "Store this token now — it is shown once and cannot be recovered.",
    }


@router.get("/auth/tokens")
def list_tokens(
    principal: Principal = Depends(require_principal), db: Session = Depends(get_db)
) -> list[dict]:
    """The caller's own tokens — metadata only, never any token material."""
    rows = (
        db.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.account_id == principal.account_id)
            .order_by(PersonalAccessToken.created_at.desc())
        )
        .unique()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "token_prefix": r.token_prefix,
            "project_id": str(r.project_id) if r.project_id else None,
            "created_at": _iso(r.created_at),
            "last_used_at": _iso(r.last_used_at),
            "revoked_at": _iso(r.revoked_at),
        }
        for r in rows
    ]


@router.delete("/auth/tokens/{token_id}")
def revoke_token(
    token_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Revoke (never delete) a token. Callers may revoke only their OWN."""
    row = db.get(PersonalAccessToken, token_id)
    if not row or row.account_id != principal.account_id:
        raise HTTPException(status_code=404, detail="token not found")
    if row.revoked_at is not None:
        return {"revoked": True, "id": str(row.id), "already_revoked": True}
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"revoked": True, "id": str(row.id)}


# ---------------------------------------------------------------------------
# Project listing / creation (requirement 6)
# ---------------------------------------------------------------------------


@router.get("/projects")
def list_my_projects(
    principal: Principal = Depends(require_principal), db: Session = Depends(get_db)
) -> dict:
    """Projects the caller is an active member of, with their role.
    Response envelope keeps room for pagination without breaking clients."""
    rows = (
        db.execute(
            select(Project, ProjectMember)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                ProjectMember.account_id == principal.account_id,
                ProjectMember.status == "active",
            )
            .order_by(Project.created_at.asc())
        )
        .unique()
        .all()
    )
    return {
        "projects": [
            {
                "id": str(p.id),
                "name": p.name,
                "goal": p.goal,
                "deadline": p.deadline.isoformat() if p.deadline else None,
                "created_at": p.created_at.isoformat(),
                "supabase_role": m.supabase_role,
                "joined_at": _iso(m.joined_at),
                # Phase 6.5 additive — may be absent on rows created before the migration.
                "github_repo": getattr(p, "github_repo", None),
            }
            for p, m in rows
        ]
    }


class ProjectCreateAuthed(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str | None = Field(default=None, max_length=2000)
    deadline: datetime | None = None
    # Phase 6.5 additive: optional GitHub repo link ("owner/repo"), display metadata only.
    github_repo: str | None = Field(default=None, max_length=200)
    # Legacy compat: accepted, ignored — the caller IS the owner (requirement 6).
    requesting_user_id: uuid.UUID | None = None


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreateAuthed,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Create a project owned by the CALLER (membership row with role='owner')."""
    from sqlalchemy.exc import IntegrityError

    existing = db.scalar(select(Project).where(Project.name == body.name))
    if existing:
        raise HTTPException(status_code=409, detail="project with this name already exists")

    project = Project(name=body.name, goal=body.goal, deadline=body.deadline, github_repo=body.github_repo)
    db.add(project)
    db.flush()
    member = ProjectMember(
        project_id=project.id,
        account_id=principal.account_id,
        supabase_role="owner",
        status="active",
    )
    db.add(member)
    # Roster identity for the creator (frozen users table stays the roster).
    user = auth_service.ensure_roster_user(db, project.id, principal)
    project.owner_user_id = user.id
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="project with this name already exists") from exc
    db.refresh(project)
    return {
        "id": str(project.id),
        "name": project.name,
        "goal": project.goal,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "created_at": project.created_at.isoformat(),
        "owner_user_id": str(user.id),
        # Phase 6.5 additive.
        "github_repo": project.github_repo,
    }

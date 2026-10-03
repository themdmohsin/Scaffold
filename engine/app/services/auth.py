"""auth.py — Phase 6 (real authentication): identity + authorization.

Replaces the placeholder "acting as" / requesting_user_id trust model:

  • IDENTITY  comes from the request itself, never a body/query field:
      - Supabase Auth JWT (Authorization: Bearer <jwt>) for dashboard users —
        verified against the Supabase JWKS (ES256/RS256, by kid) or SUPABASE_JWT_SECRET (legacy HS256),
        including audience/issuer checks against SUPABASE_URL.
      - Personal Access Token (Authorization: Bearer scaffold_<43+ chars>) for
        the OpenCode plugin / CLI / MCP calls — SHA-256 hash matched against
        personal_access_tokens.token_hash; raw tokens are never stored.
  • AUTHORIZATION is membership + role in `project_members`
    (owner | admin | member), enforced on every project route and MCP tool.

Legacy compatibility (explicitly requested): the `requesting_user_id` body
field / query param is still ACCEPTED but IGNORED for authorization — the
verified principal decides. `users.project_id` remains the per-project roster
(frozen Day 1); `project_members` adds roles on top.

No LLM anywhere in this file (repo rule #3). Deterministic by construction.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Account, PersonalAccessToken, ProjectMember, User
from app.db.session import get_db
from app.services import jwks as jwks_service

PAT_PREFIX = "scaffold_"
PAT_MIN_LEN = len(PAT_PREFIX) + 32
# DoS guard: HTTP headers are practically bounded anyway; enforce it explicitly.
MAX_TOKEN_LEN = 512


# ---------------------------------------------------------------------------
# MCP plumbing
# ---------------------------------------------------------------------------
# The MCP ASGI layer (main.py) verifies the Bearer credential before any tool
# runs and stashes the resolved Principal here; tool bodies in mcp_server.py
# read it (tool functions cannot take a Request). Set per request, reset after.
current_mcp_principal: ContextVar["Principal | None"] = ContextVar(
    "current_mcp_principal", default=None
)


class AuthUnavailable(RuntimeError):
    """The engine cannot verify ANY token (e.g. SUPABASE_JWT_SECRET unset).

    Fail closed: every authenticated route answers 503 rather than silently
    trusting an unverified credential.
    """


# ---------------------------------------------------------------------------
# Principal — who is calling, as far as this request is concerned
# ---------------------------------------------------------------------------


@dataclass
class Principal:
    """The verified caller of a request.

    account_id   — the accounts row (Supabase identity or the PAT's owner)
    via          — 'jwt' (human dashboard/session) | 'pat' (machine token)
    membership   — the PROJECT context is resolved per-request via
                   require_member(); agents attach to the owning human.
    """

    account_id: uuid.UUID
    via: str  # 'jwt' | 'pat'
    email: str | None = None
    token_id: uuid.UUID | None = None  # PAT only
    token_scoped_project_id: uuid.UUID | None = None  # PAT pinned to one project
    is_agent: bool = False  # True when the PAT resolves to an agent identity
    roster_user_id: uuid.UUID | None = None  # resolved per project (require_member)
    roles: dict = field(default_factory=dict)  # project_id -> role cache


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token_raw() -> str:
    """A fresh PAT: scaffold_ + 43 base64url chars (~256 bits). Shown ONCE."""
    return PAT_PREFIX + secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Credential verification
# ---------------------------------------------------------------------------


def _looks_like_pat(credential: str) -> bool:
    return credential.startswith(PAT_PREFIX) and len(credential) >= PAT_MIN_LEN


def _verify_jwt(credential: str) -> tuple[str, str | None]:
    """Verify a Supabase Auth JWT; return (supabase_user_id, email).

    Two clearly separated paths, chosen by the (unverified) header `alg` - and
    each path then pins `algorithms=` to exactly that family, so a token can
    never pick its own verifier:

      * ES256 / RS256 (Supabase asymmetric signing keys, the current default):
        the public key is looked up in the project's JWKS
        (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`) by the header `kid`,
        cached, refreshed on unknown kid (services/jwks.py). No secret needed.
      * HS256 (legacy shared-secret projects): verified with SUPABASE_JWT_SECRET.

    Both paths check signature, exp (required), nbf/iat, iss, aud=authenticated.
    Raises 401 on any bad token, AuthUnavailable (-> 503) when the engine
    cannot verify at all. Never a silent pass-through. Tokens are not logged.
    """
    expected_iss = (settings.supabase_url or "").strip().rstrip("/") + "/auth/v1"
    try:
        header = pyjwt.get_unverified_header(credential)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc

    alg = header.get("alg")
    key: object
    if alg == "HS256":
        secret = (settings.supabase_jwt_secret or "").strip()
        if not secret:
            raise AuthUnavailable(
                "auth unavailable: SUPABASE_JWT_SECRET is not configured on the engine"
            )
        key = secret
    elif alg in jwks_service.SUPPORTED_ALGS:
        if not (settings.supabase_url or "").strip():
            raise AuthUnavailable("auth unavailable: SUPABASE_URL is not configured (needed for JWKS)")
        kid = header.get("kid")
        if not kid or not isinstance(kid, str):
            raise HTTPException(status_code=401, detail="invalid token")
        try:
            jwk = jwks_service.cache.get_key(jwks_service.jwks_url_for(settings.supabase_url), kid)
        except jwks_service.UnknownKid as exc:
            raise HTTPException(status_code=401, detail="invalid token") from exc
        except jwks_service.JwksUnavailable as exc:
            raise AuthUnavailable("auth unavailable: cannot fetch Supabase JWKS") from exc
        if jwk.algorithm_name and jwk.algorithm_name != alg:
            raise HTTPException(status_code=401, detail="invalid token")
        key = jwk.key
    else:
        raise HTTPException(status_code=401, detail="invalid token")

    try:
        claims = pyjwt.decode(
            credential,
            key,
            algorithms=[alg],
            audience="authenticated",
            issuer=expected_iss,
            leeway=10,  # modest clock-skew allowance for exp/nbf/iat
            options={"require": ["exp", "sub"]},
        )
    except pyjwt.InvalidIssuerError as exc:
        raise HTTPException(status_code=401, detail="token issuer mismatch") from exc
    except pyjwt.InvalidAudienceError as exc:
        raise HTTPException(status_code=401, detail="token audience mismatch") from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="token missing sub claim")
    return str(sub), claims.get("email")

def _verify_pat(db: Session, credential: str) -> tuple[PersonalAccessToken, Account]:
    """Match a PAT by hash and return (token, owner_account). 401 on any miss."""
    if len(credential) > MAX_TOKEN_LEN:
        raise HTTPException(status_code=401, detail="invalid token")
    row = db.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.token_hash == hash_token(credential))
    )
    if not row or row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    account = db.get(Account, row.account_id)
    if not account:
        raise HTTPException(status_code=401, detail="token owner no longer exists")
    return row, account


def _load_account(
    db: Session, supabase_user_id: str, email: str | None, full_name: str | None = None
) -> Account:
    """Find-or-create the local accounts row for a Supabase identity."""
    account = db.scalar(select(Account).where(Account.supabase_user_id == supabase_user_id))
    if account is None:
        account = Account(
            supabase_user_id=supabase_user_id,
            email=email,
            full_name=full_name,
            auth_provider="supabase",
        )
        db.add(account)
        db.flush()
    # Keep contact fields fresh on every verified login.
    for field_name, value in (("email", email), ("full_name", full_name)):
        if value and getattr(account, field_name) != value:
            setattr(account, field_name, value)
    return account


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.strip():
        raise HTTPException(
            status_code=401, detail="missing Authorization: Bearer token"
        )
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=401, detail="missing Authorization: Bearer token"
        )
    return parts[1].strip()


def resolve_principal(db: Session, request: Request) -> Principal:
    """Verify the Authorization header and resolve the caller's Principal.

    Supports both credential kinds on the SAME scheme (Bearer), so clients do
    not need to know which is which:
      • Supabase Auth JWT  -> human identity (via='jwt')
      • PAT (scaffold_*)   -> machine identity owned by an account (via='pat')
    """
    credential = _extract_bearer(request)

    if _looks_like_pat(credential):
        token, account = _verify_pat(db, credential)
        token.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return Principal(
            account_id=account.id,
            via="pat",
            email=account.email,
            token_id=token.id,
            token_scoped_project_id=token.project_id,
        )

    supabase_user_id, email = _verify_jwt(credential)
    account = _load_account(db, supabase_user_id, email)
    return Principal(account_id=account.id, via="jwt", email=email or account.email)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def require_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Dependency: verify the caller. 401 without a valid credential, 503 when
    the engine cannot verify credentials at all (fail closed)."""
    try:
        return resolve_principal(db, request)
    except AuthUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    """Dependency for routes that stay callable without credentials
    (/health). Everything else uses require_principal."""
    try:
        return resolve_principal(db, request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise
    except AuthUnavailable:
        return None


# ---------------------------------------------------------------------------
# Project membership + roles (the authorization layer)
# ---------------------------------------------------------------------------

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)
# What each role may do:
#   member  — read everything on the project, create/edit tasks & decisions,
#             register contracts, use MCP reads, pull env values THEY are
#             granted, register agents.
#   admin   — member + member management (role changes, removal), invite
#             create/revoke, ownership transfer (with owner constraints).
#   owner   — admin + env mutations/grants/rotation and ownership bootstrap.

def _bootstrap_ids() -> set[str]:
    raw = (settings.scaffold_bootstrap_account_ids or "").strip()
    if not raw:
        return set()
    return {chunk.strip() for chunk in raw.split(",") if chunk.strip()}


def _promote_to_owner(db: Session, project_id: uuid.UUID, member: ProjectMember) -> None:
    """Demote any previous owner, promote this member (single-owner rule)."""
    for other in db.scalars(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.supabase_role == ROLE_OWNER,
            ProjectMember.id != member.id,
        )
    ).all():
        other.supabase_role = ROLE_MEMBER
    member.supabase_role = ROLE_OWNER


def ensure_roster_user(db: Session, project_id: uuid.UUID, principal: Principal) -> User:
    """Find-or-create the principal's `users` roster row for THIS project.

    Every pre-auth feature (task assignment, availability, coordination,
    MCP agent identity) reads the frozen `users` table, so an authenticated
    member gets a roster row on first authenticated touch — with a stable
    name derived from the account (email local-part / full name). Agents are
    NOT auto-created here: they are registered per-project (POST /agents).
    """
    if principal.via != "jwt":
        raise HTTPException(status_code=403, detail="only human accounts get a roster identity")
    # The account also owns its AGENT rows on this project — this is the
    # HUMAN's row, so filter agents out.
    user = db.scalar(
        select(User).where(
            User.project_id == project_id,
            User.account_id == principal.account_id,
            User.kind != "agent",
        )
    )
    if user:
        if user.membership_status == "removed":
            user.membership_status = "active"
            db.commit()
        return user
    account = db.get(Account, principal.account_id)
    name = (account.full_name if account and account.full_name else None) or (
        (account.email.split("@")[0] if account and account.email else None)
    ) or f"user-{principal.account_id.hex[:8]}"
    # Account-derived names flow into the LLM TEAM ROSTER block (one line per
    # user), so collapse whitespace runs exactly like the invite body path —
    # a newline in a Supabase full_name must not forge a roster line.
    name = re.sub(r"\s+", " ", name).strip() or f"user-{principal.account_id.hex[:8]}"
    # De-collide with a legacy same-name roster row (day5 join names):
    clash = db.scalar(
        select(User).where(User.project_id == project_id, User.name == name)
    )
    if clash:
        name = f"{name} ({principal.account_id.hex[:4]})"
    user = User(
        project_id=project_id,
        name=name,
        role="member",
        kind="developer",
        account_id=principal.account_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _load_membership(db: Session, project_id: uuid.UUID, account_id: uuid.UUID) -> ProjectMember | None:
    return db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.account_id == account_id,
        )
    )


def _bootstrap_owner(db: Session, project_id: uuid.UUID, principal: Principal) -> ProjectMember | None:
    """DEV/SEED ONLY escape hatch: auto-promote the configured bootstrap
    account to OWNER of any project it touches (creating the member row when
    the project predates auth). Empty scaffold_bootstrap_account_ids = off."""
    if principal.via != "jwt":
        return None
    supabase_user_id = _supabase_id_of(db, principal.account_id)
    if supabase_user_id not in _bootstrap_ids():
        return None
    member = _load_membership(db, project_id, principal.account_id)
    if member is None:
        member = ProjectMember(project_id=project_id, account_id=principal.account_id)
        db.add(member)
        db.flush()
    if member.supabase_role != ROLE_OWNER:
        _promote_to_owner(db, project_id, member)
        db.commit()
    return member


def _supabase_id_of(db: Session, account_id: uuid.UUID) -> str:
    account = db.get(Account, account_id)
    return account.supabase_user_id if account else f"{account_id}"


def require_member(
    db: Session, project_id: uuid.UUID, principal: Principal, *, auto_enroll: bool = True
) -> ProjectMember:
    """THE gate for every project route: caller must be an ACTIVE member of
    THIS project. 404 unknown project, 403 non-member, 403 PAT scoped to a
    different project. Returns the membership row (role included)."""
    from app.db.models import Project

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    # A PAT pinned to one project can never act on another.
    if (
        principal.token_scoped_project_id is not None
        and principal.token_scoped_project_id != project_id
    ):
        raise HTTPException(status_code=403, detail="token is scoped to a different project")

    member = _load_membership(db, project_id, principal.account_id)
    if member is None and auto_enroll:
        member = _bootstrap_owner(db, project_id, principal)
    if not member or member.status != "active":
        raise HTTPException(status_code=403, detail="you are not an active member of this project")
    return member


def require_role(
    db: Session,
    project_id: uuid.UUID,
    principal: Principal,
    minimum: str,
    *,
    auto_enroll: bool = True,
) -> ProjectMember:
    """Role gate: owner > admin > member. `minimum` in {'owner','admin','member'}.
    Used for member management (admin), invites (admin), env mutations/grants
    (owner) and ownership (owner)."""
    member = require_member(db, project_id, principal, auto_enroll=auto_enroll)
    rank = {ROLE_MEMBER: 0, ROLE_ADMIN: 1, ROLE_OWNER: 2}
    if rank.get(member.supabase_role, -1) < rank[minimum]:
        raise HTTPException(
            status_code=403,
            detail=f"this action requires the project {minimum} role",
        )
    return member


def role_of(db: Session, project_id: uuid.UUID, account_id: uuid.UUID) -> str | None:
    member = _load_membership(db, project_id, account_id)
    return member.supabase_role if member and member.status == "active" else None


def is_member(db: Session, project_id: uuid.UUID, account_id: uuid.UUID) -> bool:
    member = _load_membership(db, project_id, account_id)
    return bool(member and member.status == "active")


def set_project_role(db: Session, project_id: uuid.UUID, account_id: uuid.UUID, role: str) -> ProjectMember:
    """Create/update a membership with the given role, enforcing single-owner.
    Used by the join flow (member) and the member-management routes (admin)."""
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {', '.join(ROLES)}")
    member = _load_membership(db, project_id, account_id)
    if member is None:
        member = ProjectMember(project_id=project_id, account_id=account_id)
        db.add(member)
        db.flush()
    if role == ROLE_OWNER:
        _promote_to_owner(db, project_id, member)
    else:
        member.supabase_role = role
    member.status = "active"
    db.commit()
    db.refresh(member)
    return member


def remove_member_membership(db: Session, project_id: uuid.UUID, account_id: uuid.UUID) -> bool:
    """Drop an account's governed membership (soft delete: status='removed').

    Called by DELETE /projects/:id/members/:member_id — the frozen roster's
    `users.membership_status` alone must never be the security boundary: a
    removed teammate loses `project_members` access on the same request
    (401/403 on the next call), not just roster visibility.
    """
    member = _load_membership(db, project_id, account_id)
    if member is None or member.status == "removed":
        return False
    member.status = "removed"
    return True


def agent_for_token(db: Session, project_id: uuid.UUID, principal: Principal) -> User | None:
    """Resolve the AGENT identity for an MCP caller on a project: an agent-kind
    users row whose account_id == the PAT owner's account. Agents attach to the
    owning human's account (requirement 3)."""
    if principal.via != "pat":
        return None
    return db.scalar(
        select(User).where(
            User.project_id == project_id,
            User.kind == "agent",
            User.account_id == principal.account_id,
        )
    )


def resolve_agent_for_request(db: Session, project_id: uuid.UUID, principal: Principal) -> User:
    """MCP value-retrieval identity resolution: the caller's OWN agent row on
    the project. Never trusts a user_id from the tool call. 403 when the PAT
    owner has no agent identity on this project (the owner uses /environment
    HTTP routes instead)."""
    agent = agent_for_token(db, project_id, principal)
    if agent is None or agent.membership_status == "removed":
        raise HTTPException(
            status_code=403,
            detail="no agent identity for this token on the project — register one via POST /projects/:id/agents",
        )
    return agent


def ensure_roster_user_or_agent(
    db: Session, project_id: uuid.UUID, principal: Principal
) -> User:
    """Roster identity for any authenticated caller: humans get/find their own
    users row; PAT callers attach their registered agent's row (if any)."""
    if principal.via == "pat":
        agent = agent_for_token(db, project_id, principal)
        if agent:
            return agent
    return ensure_roster_user(db, project_id, principal)

"""auth_helper — shared credential plumbing for every verification harness.

Sets the TEST auth env (SUPABASE_JWT_SECRET / SUPABASE_URL) BEFORE app.config is
imported, mints HS256 Supabase-shaped JWTs, creates accounts + project_members
rows directly in the DB, and mints PATs via auth_service's own hashing so
harnesses exercise the production paths.

Usage pattern (in each harness, before `from app.main import app`):

    import tests.auth_helper as auth  # sets env on import

Then for a project:

    tok = auth.bootstrap(project_id=PID)          # -> owner JWT (creates account+membership)
    client.headers.update(auth.auth_headers(tok)) # all harness requests authenticate

Helpers (all import app modules lazily so env is set first):
    jwt_for(sub, email)         -> raw JWT string (HS256, Supabase-shaped)
    auth_headers(token)         -> {"Authorization": "Bearer ..."}
    ensure_account(sub, email)  -> accounts row
    set_role(pid, sub, role)    -> project_members row with that role
    pat_for(pid, role, ...)     -> (raw_token, prefix, id); raw shown once, like production
    owner_token / member_token / admin_token / outsider_token / pat
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Test auth env — MUST be set before app.config is imported (same pattern as
# GITHUB_WEBHOOK_SECRET in the existing harnesses).
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")

TEST_JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]
TEST_SUPABASE_URL = os.environ["SUPABASE_URL"]

import jwt as pyjwt  # noqa: E402  (after env set; dependency of app itself)


def jwt_for(sub: str, email: str | None = None, minutes: int = 30) -> str:
    """Mint an HS256 JWT shaped like a Supabase access token (aud=authenticated,
    iss=<SUPABASE_URL>/auth/v1, exp+sub required by the engine's verifier)."""
    import time

    now = int(time.time())
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "iss": f"{TEST_SUPABASE_URL}/auth/v1",
        "iat": now,
        "exp": now + minutes * 60,
        "email": email or f"{sub[:12]}@test.example",
        "role": "authenticated",
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _db():
    from app.db.session import _init

    return _init()()


# Friendly roster names for the standard harness accounts (the roster name is
# derived from accounts.full_name / email local-part).
STANDARD_NAMES = {
    "11111111-1111-4111-8111-111111111111": "Owner",
    "22222222-2222-4222-8222-222222222222": "Admin",
    "33333333-3333-4333-8333-333333333333": "Member",
    "44444444-4444-4444-8444-444444444444": "Outsider",
}


def ensure_account(sub: str, email: str | None = None, full_name: str | None = None):
    """Find-or-create the accounts row for a Supabase user id."""
    from app.db.models import Account

    from sqlalchemy import select

    full_name = full_name or STANDARD_NAMES.get(sub)
    db = _db()
    try:
        acct = db.scalar(select(Account).where(Account.supabase_user_id == sub))
        if acct is None:
            acct = Account(
                supabase_user_id=sub,
                email=email or f"{sub[:12]}@test.example",
                full_name=full_name,
            )
            db.add(acct)
            db.commit()
            db.refresh(acct)
        elif full_name and not acct.full_name:
            acct.full_name = full_name
            db.commit()
            db.refresh(acct)
        return acct
    finally:
        db.close()


def extra_member(name: str, email: str | None = None) -> tuple[str, str]:
    """A FRESH account (no memberships anywhere) whose roster name derives from
    its account full_name, plus its JWT. Returns (jwt, sub).

    Use this for join flows: identity now comes from the TOKEN, so each joining
    teammate needs its own bearer token instead of a `name` in the body.
    """
    sub = str(uuid.uuid4())
    ensure_account(
        sub,
        email or f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}@test.example",
        full_name=name,
    )
    return jwt_for(sub), sub


def role_of(project_id: str, sub: str) -> str | None:
    """The governed project_members role for a Supabase user id (None if not a
    member) — lets harnesses assert role transitions without raw SQL."""
    from app.services import auth as auth_service

    from sqlalchemy import select

    from app.db.models import Account

    db = _db()
    try:
        acct = db.scalar(select(Account).where(Account.supabase_user_id == sub))
        if acct is None:
            return None
        return auth_service.role_of(db, uuid.UUID(project_id), acct.id)
    finally:
        db.close()


def set_role(project_id: str, sub: str, role: str):
    """Create/update an ACTIVE project_members row (role in owner/admin/member).
    `sub` is the Supabase user id; resolved to the accounts row first."""
    from app.services import auth as auth_service

    account = ensure_account(sub)
    db = _db()
    try:
        return auth_service.set_project_role(db, uuid.UUID(project_id), account.id, role)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Per-project credential cache
# ---------------------------------------------------------------------------

OWNER_SUB = "11111111-1111-4111-8111-111111111111"
ADMIN_SUB = "22222222-2222-4222-8222-222222222222"
MEMBER_SUB = "33333333-3333-4333-8333-333333333333"
OUTSIDER_SUB = "44444444-4444-4444-8444-444444444444"
AGENT_SUB = "55555555-5555-4555-8555-555555555555"  # agent-kind roster identity


class ProjectAuth:
    """Credentials for one project: owner/admin/member JWTs, an outsider JWT,
    and a PAT (raw shown once) attached to the owner's account."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._pats: list[tuple[str, str]] = []

    def bootstrap(self) -> str:
        """Idempotently create all accounts + memberships. Returns the owner JWT."""
        ensure_account(OWNER_SUB, full_name="Owner")
        ensure_account(ADMIN_SUB, full_name="Admin")
        ensure_account(MEMBER_SUB, full_name="Member")
        ensure_account(OUTSIDER_SUB, full_name="Outsider")
        set_role(self.project_id, OWNER_SUB, "owner")
        set_role(self.project_id, ADMIN_SUB, "admin")
        set_role(self.project_id, MEMBER_SUB, "member")
        return self.owner_token()

    def owner_token(self) -> str:
        return jwt_for(OWNER_SUB)

    def admin_token(self) -> str:
        return jwt_for(ADMIN_SUB)

    def member_token(self) -> str:
        return jwt_for(MEMBER_SUB)

    def outsider_token(self) -> str:
        """Valid JWT but NOT a member of this project (may belong to others)."""
        return jwt_for(OUTSIDER_SUB)

    def pat(
        self,
        scoped_to_project: bool = False,
        name: str = "harness token",
        sub: str = OWNER_SUB,
    ) -> tuple[str, str]:
        """(raw, prefix). Attached to an account (default: the owner's) — pass
        `sub` for a different account, e.g. a developer's PAT that speaks as
        their own registered agent. Optionally scoped to this project."""
        from app.db.models import PersonalAccessToken

        acct = ensure_account(sub)
        from app.services import auth as auth_service

        raw = auth_service.new_token_raw()
        db = _db()
        try:
            row = PersonalAccessToken(
                account_id=acct.id,
                name=name,
                token_hash=auth_service.hash_token(raw),
                token_prefix=raw[:14],
                project_id=uuid.UUID(self.project_id) if scoped_to_project else None,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            self._pats.append((raw, row.token_prefix))
            return raw, row.token_prefix
        finally:
            db.close()

    def revoke_pat(self, prefix: str) -> None:
        from sqlalchemy import select

        from app.db.models import PersonalAccessToken

        db = _db()
        try:
            row = db.scalar(
                select(PersonalAccessToken).where(PersonalAccessToken.token_prefix == prefix)
            )
            if row:
                from datetime import datetime, timezone

                row.revoked_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()


def bootstrap(project_id: str) -> tuple[ProjectAuth, str]:
    """One-call setup: memberships + a ProjectAuth. Returns (auth, owner_jwt)."""
    pa = ProjectAuth(project_id)
    return pa, pa.bootstrap()

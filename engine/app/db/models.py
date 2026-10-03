"""SQLAlchemy models — mirror engine/app/db/schema.sql and docs/SCHEMA.md exactly.

Frozen Day 1 (2026-09-23). Do not rename columns; docs/SCHEMA.md is the contract.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Phase 3 (team collaboration) addition — nullable, additive per the
    # docs/SCHEMA.md freeze rule. See migrate_phase3.sql.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    # Phase 3 (team collaboration) additions — all nullable/defaulted, additive
    # per docs/SCHEMA.md freeze rule. See migrate_phase3.sql.
    kind: Mapped[str] = mapped_column(String, default="developer")  # 'developer' | 'agent'
    agent_provider: Mapped[str | None] = mapped_column(Text)
    agent_model: Mapped[str | None] = mapped_column(Text)
    agent_session_id: Mapped[str | None] = mapped_column(Text)
    membership_status: Mapped[str] = mapped_column(String, default="active")  # 'active' | 'removed'
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Phase 6 (real auth) addition — nullable: legacy roster rows and agents
    # keep working without an account. See migrate_auth.sql.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 'todo' | 'in_progress' | 'review' | 'done' — 'review' added Phase 2
    # (migrate_phase2.sql widens the CHECK; old values untouched).
    status: Mapped[str] = mapped_column(String, default="todo")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Phase 2 (Project Control Center) additions — all nullable/defaulted,
    # additive per docs/SCHEMA.md freeze rule. See migrate_phase2.sql.
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String, default="medium")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text)
    made_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Day 3 (pgvector): embedding of `text`, 1536 dims — see migrate_day3.sql.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))


class DecisionAffectsTask(Base):
    __tablename__ = "decision_affects_tasks"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id"), primary_key=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), primary_key=True
    )


class ApiContract(Base):
    __tablename__ = "api_contracts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    route: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    request_schema: Mapped[dict | None] = mapped_column(JSONB)
    response_schema: Mapped[dict | None] = mapped_column(JSONB)
    created_by_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Day 3 (pgvector): embedding of "METHOD /route", 1536 dims — migrate_day3.sql.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))


class Commit(Base):
    __tablename__ = "commits"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    sha: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    files_changed: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Blocker(Base):
    __tablename__ = "blockers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id")
    )
    description: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Phase 5 (secure environment) — metadata + permission tables ONLY. The secret
# VALUE layer (scaffold_secrets.environment_secrets + .keyring) is deliberately
# NOT mapped as an ORM model: services/secret_store.py is the single writer and
# reader of ciphertext, so no accidental `SELECT *` can ever drag a value into
# application memory or a response serializer (see docs/SCHEMA.md Phase 5).
# ---------------------------------------------------------------------------


class EnvironmentVariable(Base):
    __tablename__ = "environment_variables"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EnvironmentAccess(Base):
    __tablename__ = "environment_access"

    id: Mapped[uuid.UUID] = _uuid_pk()
    environment_variable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environment_variables.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Phase 6 (real authentication) — additive tables per docs/SCHEMA.md. The
# engine is the ONLY writer of these rows; the service key bypasses RLS (see
# migrate_auth.sql). No frozen table/column was renamed or removed.
# ---------------------------------------------------------------------------


class Account(Base):
    """One authenticated identity — mirrors a Supabase auth.users row
    (email + password or GitHub OAuth). Project membership lives in
    project_members; the per-project roster stays in `users` (frozen)."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    supabase_user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    auth_provider: Mapped[str] = mapped_column(String, default="email")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMember(Base):
    """Membership WITH ROLE (owner/admin/member) — the authorization source of
    truth. One account may hold at most one active role per project."""

    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    # 'owner' | 'admin' | 'member' — named supabase_role to avoid the
    # Postgres-reserved `role` column collision.
    supabase_role: Mapped[str] = mapped_column(String, default="member")
    status: Mapped[str] = mapped_column(String, default="active")  # 'active' | 'removed'
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    # Which invite row redeemed this membership ("shows who joined", req. 4).
    invite_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invites.id"))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PersonalAccessToken(Base):
    """Machine credential for the OpenCode plugin / CLI / MCP calls. Only the
    SHA-256 HASH of the token is stored — the raw value is shown once at
    creation and can never be recovered. Revocation flips revoked_at."""

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, default="token")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(Text, default="")
    # Optional single-project scoping for tokens used by CI or scoped tooling.
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    scopes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invite(Base):
    """A real, revocable invitation row (replaces the stateless Day-5 signed
    code). The code FORMAT stays `project_id.expiry.sig` so old links work;
    redemption now requires a live, unrevoked, unexpired row."""

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    supabase_role: Mapped[str] = mapped_column(String, default="member")
    max_uses: Mapped[int | None] = mapped_column()
    use_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InviteRedemption(Base):
    """One row per join through an invite — the audit trail of "who joined via
    which invite" (requirement 4)."""

    __tablename__ = "invite_redemptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invites.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    roster_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

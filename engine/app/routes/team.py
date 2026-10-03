"""Team routes — Phase 3 (team collaboration).

Adds project membership visibility, agent identity, and project ownership on
top of the Day 5 `users` table (no second roster/task/activity system — repo
rule). New, additive endpoints only:

GET   /projects/{id}/members            -> roster with computed activity status
PATCH /projects/{id}/members/{member_id} -> change role/kind (owner-gated once an owner exists)
DELETE /projects/{id}/members/{member_id} -> soft-remove (membership_status='removed')
POST  /projects/{id}/agents             -> register/update an AI agent participant
POST  /projects/{id}/owner              -> bootstrap or transfer project ownership

Invitations already exist (Day 5, `POST /projects/{id}/invite` + `POST
/projects/join`, stateless signed codes) — not duplicated here.

Authorization note: this repo has no authentication system yet (see
AGENTS.md / HANDOFF — every existing route is unauthenticated). Per the
Phase 3 brief ("do not overengineer permissions yet", "smallest architecture
compatible with existing system and future auth"), owner-gated mutations
accept a plain `requesting_user_id` field and compare it to
`projects.owner_user_id`. This is a placeholder authorization hook, not real
security — it becomes a JWT-derived identity once Supabase Auth (or similar)
is wired in. Until a project has an owner, these mutations are open, matching
every other route's current trust model.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, Blocker, Decision, Event, Project, ProjectMember, Task, User
from app.db.session import get_db
from app.services import auth as auth_service
from app.services import team as team_service
from app.services.auth import Principal, require_member, require_principal, require_role

router = APIRouter(tags=["team"])

ALLOWED_ROLES = {"owner", "member", "agent"}


def _require_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _require_owner_if_set(project: Project, requesting_user_id: uuid.UUID | None) -> None:
    """Owner-gate a mutation. No-op (open) if the project has no owner yet —
    see the module docstring for why this isn't real auth."""
    if project.owner_user_id is None:
        return
    if requesting_user_id is None or requesting_user_id != project.owner_user_id:
        raise HTTPException(
            status_code=403,
            detail="only the project owner can perform this action",
        )


def _row(obj, *fields: str) -> dict:
    return {f: getattr(obj, f) for f in fields}


def _load_roster_rows(db: Session, project_id: uuid.UUID) -> dict:
    """Bounded fan-out for the roster computation (repo rule #6: small,
    targeted reads — this mirrors availability.compute_roster's approach of
    loading plain rows once, then running pure functions over them)."""
    users = db.scalars(
        select(User).where(User.project_id == project_id).order_by(User.name.asc())
    ).all()
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
    decisions = db.scalars(select(Decision).where(Decision.project_id == project_id)).all()
    events = db.scalars(
        select(Event)
        .where(Event.project_id == project_id)
        .order_by(Event.created_at.desc())
        .limit(200)
    ).all()
    open_blocker_task_ids = {
        b.task_id
        for b in db.scalars(
            select(Blocker).where(
                Blocker.project_id == project_id,
                Blocker.resolved.is_(False),
                Blocker.task_id.is_not(None),
            )
        ).all()
        if b.task_id is not None
    }

    return {
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "role": u.role,
                "kind": u.kind,
                "agent_provider": u.agent_provider,
                "agent_model": u.agent_model,
                "agent_session_id": u.agent_session_id,
                "membership_status": u.membership_status,
                "joined_at": u.joined_at,
                "account_id": u.account_id,
            }
            for u in users
        ],
        "tasks": [_row(t, "id", "owner_id", "status", "title", "created_at") for t in tasks],
        "decisions": [_row(d, "made_by", "created_at") for d in decisions],
        "events": [{"payload": e.payload, "created_at": e.created_at} for e in events],
        "open_blocker_task_ids": open_blocker_task_ids,
    }


def _to_str_id(row: dict, key: str = "id") -> dict:
    row = dict(row)
    if row.get(key) is not None:
        row[key] = str(row[key])
    return row


@router.get("/projects/{project_id}/members")
def list_members(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_member(db, project_id, principal)
    data = _load_roster_rows(db, project_id)
    # Stringify UUIDs before the pure layer runs (it compares by value, str is fine).
    users = [
        {**u, "id": str(u["id"])} for u in data["users"]
    ]
    tasks = [
        {**t, "id": str(t["id"]), "owner_id": str(t["owner_id"]) if t["owner_id"] else None}
        for t in data["tasks"]
    ]
    decisions = [
        {**d, "made_by": str(d["made_by"]) if d["made_by"] else None} for d in data["decisions"]
    ]
    events = data["events"]
    open_blocker_task_ids = {str(x) for x in data["open_blocker_task_ids"]}

    now = datetime.now(timezone.utc)
    rows = [
        team_service.build_member_row(u, now, tasks, decisions, events, open_blocker_task_ids)
        for u in users
        if u["membership_status"] != "removed"
    ]

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    # ADDITIVE (Phase 6): flag the caller's own HUMAN roster row so the dashboard
    # can act "as me" without an identity picker. Computed from the verified
    # principal; no account ids are exposed.
    my_ids = {
        str(u["id"])
        for u in data["users"]
        if u["account_id"] == principal.account_id and u["kind"] != "agent"
    }
    for r in rows:
        r["joined_at"] = _iso(r["joined_at"])
        r["last_activity_at"] = _iso(r["last_activity_at"])
        r["is_me"] = r["id"] in my_ids
    return rows


class MemberUpdate(BaseModel):
    role: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, pattern="^(developer|agent)$")
    # LEGACY: accepted but ignored — the verified principal decides (Phase 6).
    requesting_user_id: uuid.UUID | None = None
    # ADDITIVE (Phase 6): the governed membership role ('owner'|'admin'|'member').
    supabase_role: str | None = Field(default=None, pattern="^(owner|admin|member)$")


@router.patch("/projects/{project_id}/members/{member_id}")
def update_member(
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    _require_project(db, project_id)
    user = db.scalar(
        select(User).where(User.id == member_id, User.project_id == project_id)
    )
    if not user:
        raise HTTPException(status_code=404, detail="member not found")
    # Member management = ADMIN+ (Phase 6 role model).
    require_role(db, project_id, principal, "admin")

    changed: dict = {}
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changed["role"] = body.role
    if body.kind is not None and body.kind != user.kind:
        user.kind = body.kind
        changed["kind"] = body.kind
    if body.supabase_role is not None:
        member_row = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.account_id == user.account_id,
            )
        ) if user.account_id else None
        if not member_row:
            raise HTTPException(
                status_code=400,
                detail="that roster member has no account-backed membership to re-role",
            )
        if member_row.supabase_role != body.supabase_role:
            if body.supabase_role == "owner":
                auth_service.set_project_role(db, project_id, user.account_id, "owner")
            else:
                member_row.supabase_role = body.supabase_role
            changed["supabase_role"] = body.supabase_role

    if changed:
        db.add(
            Event(
                project_id=project_id,
                type="member_role_changed",
                payload={"user_id": str(member_id), **changed},
            )
        )
        db.commit()
        db.refresh(user)

    return {
        "id": str(user.id),
        "name": user.name,
        "role": user.role,
        "kind": user.kind,
        "membership_status": user.membership_status,
    }


@router.delete("/projects/{project_id}/members/{member_id}", status_code=200)
def remove_member(
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    requesting_user_id: uuid.UUID | None = None,  # LEGACY: ignored (verified principal decides)
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    project = _require_project(db, project_id)
    user = db.scalar(
        select(User).where(User.id == member_id, User.project_id == project_id)
    )
    if not user:
        raise HTTPException(status_code=404, detail="member not found")
    if project.owner_user_id == member_id:
        raise HTTPException(
            status_code=400,
            detail="cannot remove the project owner — transfer ownership first",
        )
    require_role(db, project_id, principal, "admin")

    user.membership_status = "removed"
    # The frozen roster flag is not the security boundary: drop the governed
    # project_members row too, so access ends on this request (Phase 6).
    if user.account_id is not None:
        auth_service.remove_member_membership(db, project_id, user.account_id)
    db.add(
        Event(
            project_id=project_id,
            type="member_removed",
            payload={"user_id": str(member_id), "name": user.name},
        )
    )
    db.commit()
    return {"id": str(member_id), "membership_status": "removed"}


class AgentRegister(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=200)


@router.post("/projects/{project_id}/agents", status_code=201)
def register_agent(
    project_id: uuid.UUID,
    body: AgentRegister,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Upsert an AI agent as a first-class project participant. Provider/model
    are free text — never hardcode a vendor into the data model (Phase 3 brief).
    Phase 6: the agent's users row is ACCOUNT-BACKED — it attaches to the
    CALLER's account so the caller's PAT speaks AS this agent over MCP."""
    require_member(db, project_id, principal)

    existing = db.scalar(
        select(User).where(
            User.project_id == project_id,
            User.kind == "agent",
            User.name == body.name,
        )
    )
    is_new = existing is None
    if existing:
        existing.agent_provider = body.provider
        existing.agent_model = body.model
        existing.agent_session_id = body.session_id
        user = existing
    else:
        user = User(
            project_id=project_id,
            name=body.name,
            role="agent",
            kind="agent",
            agent_provider=body.provider,
            agent_model=body.model,
            agent_session_id=body.session_id,
            account_id=principal.account_id,  # Phase 6: agent attaches to its owner's account
        )
        db.add(user)
        db.flush()

    db.add(
        Event(
            project_id=project_id,
            type="agent_registered" if is_new else "agent_session_updated",
            payload={
                "user_id": str(user.id),
                "name": user.name,
                "provider": user.agent_provider,
                "model": user.agent_model,
            },
        )
    )
    db.commit()
    db.refresh(user)
    return {
        "id": str(user.id),
        "name": user.name,
        "kind": user.kind,
        "agent_provider": user.agent_provider,
        "agent_model": user.agent_model,
        "agent_session_id": user.agent_session_id,
        "is_new": is_new,
    }


class OwnerClaim(BaseModel):
    # The roster user being made owner (must belong to an ACCOUNT for the
    # governed role to be set; legacy roster-only users keep legacy behavior).
    user_id: uuid.UUID
    # LEGACY: accepted but ignored — the verified principal decides (Phase 6).
    requesting_user_id: uuid.UUID | None = None


@router.post("/projects/{project_id}/owner")
def set_owner(
    project_id: uuid.UUID,
    body: OwnerClaim,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Bootstrap ownership (first call, no owner yet) or transfer it. Phase 6:
    caller must be the current OWNER (or bootstrap when none exists); the
    governed `project_members` role moves with the legacy owner_user_id."""
    project = _require_project(db, project_id)
    user = db.scalar(
        select(User).where(User.id == body.user_id, User.project_id == project_id)
    )
    if not user:
        raise HTTPException(status_code=400, detail="user_id is not a member of this project")

    if user.membership_status == "removed":
        raise HTTPException(
            status_code=400,
            detail="that member was removed from the project — re-invite them first",
        )

    if project.owner_user_id is not None:
        require_role(db, project_id, principal, "owner")
        if user.account_id is None:
            raise HTTPException(
                status_code=400,
                detail="ownership can only move to an account-backed member",
            )

    project.owner_user_id = body.user_id
    if user.role not in ("owner",):
        user.role = "owner"
    if user.account_id is not None:
        auth_service.set_project_role(db, project_id, user.account_id, "owner")
    db.add(
        Event(
            project_id=project_id,
            type="project_owner_set",
            payload={"user_id": str(body.user_id)},
        )
    )
    db.commit()
    return {"project_id": str(project_id), "owner_user_id": str(body.user_id)}

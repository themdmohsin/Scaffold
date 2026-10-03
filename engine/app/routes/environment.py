"""Environment routes — Phase 5 (SECURE ENVIRONMENT & CONTEXT).

Minimal additive API surface. The three layers stay separate end to end:

    Layer 1  metadata     GET/POST/PATCH/DELETE  .../environment[...]      (no values)
    Layer 2  permissions  POST/DELETE            .../environment/access    (no values)
    Layer 3  values       POST .../environment/request + .../environment/pull  (the ONLY
                          routes that may return a value — after membership +
                          grant checks, with an audit event, value-free payloads)

Hard rules enforced here (Phase 5 brief §Security requirements):
  • Secret values are NEVER part of any list/GET response — the dashboard can
    show configuration STATUS only.
  • GET /projects/:id/context (Phase 1) is untouched: it gains nothing env-
    related, so the always-on summary and MCP get_project_context() can never
    leak a value.
  • Audit events (`events` table) record WHO accessed WHAT and the OUTCOME —
    never a value (payloads are built in services/environment.py where values
    are not even in scope).
  • The `value` body fields below are consumed by the secret store within the
    request and never echoed back in any response.

Authorization model (Day 8 real auth): every route requires a verified caller
via `Authorization: Bearer` (Supabase JWT for humans, PAT `scaffold_*` for
agents/CLI — see services/auth.py) AND active project membership. Env
MUTATIONS and GRANTS additionally require the project OWNER role. Identity is
never taken from a body/query field: legacy `requesting_user_id` / `user_id`
body fields are still ACCEPTED but IGNORED. Values are returned to authorized
runtimes (agents via MCP, developers via env pull) — never rendered by the
dashboard.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EnvironmentAccess, EnvironmentVariable, User
from app.db.session import get_db
from app.services import auth as auth_service
from app.services import environment as env
from app.services.auth import Principal, require_principal, require_role

router = APIRouter(prefix="/projects/{project_id}/environment", tags=["environment"])


# ---------------------------------------------------------------------------
# Layer 1 — metadata (status comes from a boolean, never a value)
# ---------------------------------------------------------------------------
def _project(db: Session, project_id: uuid.UUID):
    return env.get_project(db, project_id)


# NOTE: registered at "/" under the /projects/{id}/environment prefix. A bare
# "" path does not produce a matchable route in Starlette, so both the
# slash-less (…/environment) and slashed (…/environment/) forms are declared.
@router.get("")
@router.get("/")
def get_environment(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Configuration status for the project. Every row is metadata + a computed
    `configured` boolean + a display status. There is NO field in this response
    that could carry a secret value — pinned by tests/test_phase5.py."""
    auth_service.require_member(db, project_id, principal)
    project = _project(db, project_id)
    variables = env.list_variables(db, project_id)
    return {
        "project_id": str(project_id),
        "project_name": project.name,
        "variables": [
            {**v, "status": env.status_of(v, v["configured"]), "display_status": env.display_status(env.status_of(v, v["configured"]))}
            for v in variables
        ],
        "summary": {
            "total": len(variables),
            "configured": sum(1 for v in variables if v["configured"]),
            "required_missing": sum(
                1 for v in variables if v["required"] and not v["configured"]
            ),
            "optional_missing": sum(
                1 for v in variables if not v["required"] and not v["configured"]
            ),
        },
    }


class VariableCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    required: bool = False
    is_secret: bool = True
    created_by: uuid.UUID | None = None
    # Optional initial value — consumed by the secret store in-request, never
    # echoed back. Omit for "define now, configure later".
    value: str | None = Field(default=None, max_length=8000)


@router.post("/variables", status_code=201)
def create_variable(
    project_id: uuid.UUID,
    body: VariableCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_role(db, project_id, principal, auth_service.ROLE_OWNER)
    project = _project(db, project_id)
    if body.created_by:
        creator = db.get(User, body.created_by)
        if not creator or creator.project_id != project_id:
            raise HTTPException(status_code=400, detail="created_by is not a member of this project")
    # Owner gate passed above; satisfy the service-level legacy check without
    # trusting any body field (open when the project has no owner yet).
    env.require_owner_if_set(project, project.owner_user_id)
    return env.define_variable(
        db,
        project_id,
        key=body.key,
        description=body.description,
        required=body.required,
        is_secret=body.is_secret,
        created_by=body.created_by,
        value=body.value,
    )


class VariablePatch(BaseModel):
    # Metadata edits (owner-gated once an owner exists).
    description: str | None = Field(default=None, max_length=500)
    required: bool | None = None
    is_secret: bool | None = None
    # Setting a value = initial configuration; setting it on a configured
    # variable = ROTATION (permissions and metadata untouched — Feature 9).
    value: str | None = Field(default=None, max_length=8000)
    value_changed: bool = False
    requesting_user_id: uuid.UUID | None = None


@router.patch("/variables/{variable_id}")
def patch_variable(
    project_id: uuid.UUID,
    variable_id: uuid.UUID,
    body: VariablePatch,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_role(db, project_id, principal, auth_service.ROLE_OWNER)
    project = _project(db, project_id)
    var = env.get_variable(db, project_id, variable_id)
    if body.value_changed and body.value is None:
        raise HTTPException(status_code=400, detail="value_changed=true requires a value")
    return env.update_variable(
        db,
        project,
        var,
        project.owner_user_id,
        description=body.description,
        required=body.required,
        is_secret=body.is_secret,
        value=body.value,
        value_changed=body.value_changed,
    )


@router.delete("/variables/{variable_id}")
def delete_variable(
    project_id: uuid.UUID,
    variable_id: uuid.UUID,
    requesting_user_id: uuid.UUID | None = None,  # legacy, ignored
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_role(db, project_id, principal, auth_service.ROLE_OWNER)
    project = _project(db, project_id)
    var = env.get_variable(db, project_id, variable_id)
    return env.remove_variable(db, project, var, project.owner_user_id)


# ---------------------------------------------------------------------------
# Layer 2 — per-variable grants (access control)
# ---------------------------------------------------------------------------


class AccessGrant(BaseModel):
    environment_variable_id: uuid.UUID
    user_id: uuid.UUID
    granted_by: uuid.UUID | None = None
    # Owner-gate: matching projects.owner_user_id once an owner exists (the
    # repo-wide placeholder model — see module docstring).
    requesting_user_id: uuid.UUID | None = None


@router.post("/access")
def grant_access(
    project_id: uuid.UUID,
    body: AccessGrant,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_role(db, project_id, principal, auth_service.ROLE_OWNER)
    project = _project(db, project_id)
    var = env.get_variable(db, project_id, body.environment_variable_id)
    # granted_by is IDENTITY, taken from the verified principal — a body value
    # could otherwise forge the audit trail (or break the users.id FK). Legacy
    # field: accepted, ignored.
    body.granted_by = auth_service.ensure_roster_user(db, project_id, principal).id
    return env.grant_access(db, project, var, body.user_id, body.granted_by)


@router.get("/access")
def list_access(
    project_id: uuid.UUID,
    environment_variable_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Grant list for one variable (metadata only — who may access what)."""
    auth_service.require_member(db, project_id, principal)
    _project(db, project_id)
    var = env.get_variable(db, project_id, environment_variable_id)
    return env.grants_for_variable(db, var)


@router.delete("/access/{grant_id}")
def revoke_access(
    project_id: uuid.UUID,
    grant_id: uuid.UUID,
    requesting_user_id: uuid.UUID | None = None,  # legacy, ignored
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_role(db, project_id, principal, auth_service.ROLE_OWNER)
    project = _project(db, project_id)
    found = env.get_grant(db, project_id, grant_id)
    if not found:
        raise HTTPException(status_code=404, detail="access grant not found")
    grant, var = found
    return env.revoke_access(db, project, grant, var, project.owner_user_id)


# ---------------------------------------------------------------------------
# Layer 3 — the request flow (Feature 4) + env pull (Feature 5)
# ---------------------------------------------------------------------------


class SecretRequest(BaseModel):
    """One variable per request. The response is the ONLY place a value is ever
    returned — and only after auth + membership + grant checks pass. Legacy
    `user_id` is ACCEPTED but IGNORED: identity comes from the Bearer token
    (the caller's roster user, or their registered agent for PAT callers)."""
    key: str = Field(min_length=1, max_length=120)
    user_id: uuid.UUID | None = None


@router.post("/request")
def request_secret(
    project_id: uuid.UUID,
    body: SecretRequest,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_member(db, project_id, principal)
    project = _project(db, project_id)
    user = auth_service.ensure_roster_user_or_agent(db, project_id, principal)
    var = db.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == body.key.strip(),
        )
    )
    if not var:
        raise HTTPException(status_code=404, detail="environment variable not found")
    return env.request_secret(db, project, var, user)


class EnvPullRequest(BaseModel):
    """`scaffold env pull` transport. Returns every value the caller is
    AUTHORIZED for — nothing else, and never to the dashboard UI. Legacy
    `user_id` is ACCEPTED but IGNORED (identity from the Bearer token)."""
    user_id: uuid.UUID | None = None


@router.post("/pull")
def pull_environment(
    project_id: uuid.UUID,
    body: EnvPullRequest,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.require_member(db, project_id, principal)
    project = _project(db, project_id)
    user = auth_service.ensure_roster_user_or_agent(db, project_id, principal)
    payload = env.pull_environment(db, project, user)
    payload["file_body"] = env.pull_file_body(project.name, payload["values"])
    payload["filename"] = ".env.scaffold"
    payload["warnings"] = [
        "Review before use. NEVER commit this file — add it to .gitignore.",
    ]
    return payload


# ---------------------------------------------------------------------------
# Template (Feature 6) + audit (Feature 8)
# ---------------------------------------------------------------------------


@router.get("/template")
def environment_template(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """`.env.example` contents: variable names with EMPTY values + per-variable
    descriptions. Deterministic, value-free."""
    auth_service.require_member(db, project_id, principal)
    project = _project(db, project_id)
    template = env.build_template(db, project_id)
    template["project_name"] = project.name
    template["content"] = (
        f"# .env.example — {project.name}\n"
        "# Variable names only, NEVER values (Scaffold Phase 5).\n"
        "# Descriptions: see GET /projects/{id}/environment.\n"
        + template["content"]
    )
    return template


@router.get("/audit")
def environment_audit(
    project_id: uuid.UUID,
    limit: int = 50,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Access audit trail — who requested/retrieved/was denied what, when.
    Payloads are value-free by construction (built in services/environment.py)."""
    auth_service.require_member(db, project_id, principal)
    _project(db, project_id)
    return {"project_id": str(project_id), "events": env.audit_log(db, project_id, limit)}

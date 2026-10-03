"""Coordination routes — Phase 4 (intelligent coordination).

Additive, project-scoped endpoints answering the Phase 4 questions. All of the
decision logic is deterministic (services/coordination.py over plain rows from
services/coordination_data.py — repo rule #3); the only LLM touchpoint is the
one-sentence `explanation` phrasing via reasoning.explain_recommendation, which
is fail-open and optional on every endpoint.

    GET  /projects/:id/tasks/ready                     who can start what, now
    GET  /projects/:id/recommendations                 per-member recommendations
    GET  /projects/:id/recommendations/next            one best recommendation
    POST /projects/:id/recommendations/next            project-level next action
    GET  /projects/:id/coordination                    panel payload (all sections)
    POST /projects/:id/tasks/:task_id/accept-recommendation  human override: accept
    POST /projects/:id/tasks/:task_id/reject-recommendation  human override: reject

Authorization: this repo has no authentication yet (see routes/team.py's
docstring — every existing route is unauthenticated). A project's data is only
readable through its project UUID, matching the trust model of every other
route; member scoping is via `user_id` request params validated against the
project roster, so one project's recommendations never leak another's. These
become real session identities once auth is wired in.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Project, Task, User
from app.db.session import get_db
from app.routes.tasks import _task_out
from app.services import coordination as coord
from app.services import coordination_data as cdata
from app.services import reasoning
from app.services.auth import Principal, require_member, require_principal

router = APIRouter(prefix="/projects/{project_id}", tags=["coordination"])

MAX_READY = 50


def _require_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _require_member(db: Session, project_id: uuid.UUID, user_id: str | None) -> User | None:
    """Resolve + validate a requester against the project roster. None is fine
    (anonymous/read-only); an id that isn't on THIS project is a 400 — the same
    rule the frozen task-assignment path applies to owner_id."""
    if not user_id:
        return None
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="user_id is not a valid UUID")
    user = db.get(User, uid)
    if not user or user.project_id != project_id:
        raise HTTPException(status_code=400, detail="user_id is not a member of this project")
    return user


def _augment_ready_rows(
    tasks: list[dict],
    dep_edges: list[tuple[str, str]],
    dependency_counts: dict[str, int],
    users: list[dict],
) -> list[dict]:
    """Attach owner names + dependency titles to ready-task rows for the UI."""
    users_by_id = {u["id"]: u for u in users}
    by_id = {t["id"]: t for t in tasks}
    for row in tasks:
        row["dependency_count"] = dependency_counts.get(row["id"], 0)
        owner = users_by_id.get(row.get("owner_id") or "")
        row["owner_name"] = owner["name"] if owner else None
        row["owner_kind"] = owner.get("kind") if owner else None
    dep_map: dict[str, list[dict]] = {}
    for task_id, depends_on in dep_edges:
        dep = by_id.get(depends_on)
        if dep:
            dep_map.setdefault(task_id, []).append(
                {"id": dep["id"], "title": dep["title"], "status": dep["status"]}
            )
    for row in tasks:
        row["dependencies"] = dep_map.get(row["id"], [])
    return tasks


@router.get("/tasks/ready")
def get_ready_tasks(
    project_id: uuid.UUID,
    user_id: str | None = None,
    explain: bool = False,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Deterministic ready-task detection. Ready = todo, not blocked, all
    dependencies complete — assigned OR unassigned (unassigned = claimable).
    Ordered by the explainable ranker; `reasons` on every row."""
    require_member(db, project_id, principal)
    requester = _require_member(db, project_id, user_id)
    snap = cdata.load_snapshot(db, project_id)

    incomplete = coord.incomplete_deps_per_task(snap["tasks"], snap["dep_edges"])
    downstream = coord.downstream_open_counts(snap["tasks"], snap["dep_edges"])

    ready = [
        t
        | {"dependency_count": snap["dependency_counts"].get(t["id"], 0)}
        for t in snap["tasks"]
        if coord.is_ready(t, snap["open_blocker_task_ids"], incomplete.get(t["id"], set()))
    ]
    ranked = coord.rank_candidates(
        ready, requester.id if requester else None, downstream, cdata.utcnow(),
        incomplete, snap["open_blocker_task_ids"],
    )[:MAX_READY]

    ready_ids = [t["id"] for t in ready]
    out = _augment_ready_rows(ranked, snap["dep_edges"], snap["dependency_counts"], snap["users"])
    for row in out:
        row["ready"] = True
    return {
        "ready_tasks": out,
        "count": len(out),
        "ready_task_ids": [t["id"] for t in out],
        "unassigned_ready_count": sum(1 for t in ready if not t.get("owner_id")),
        "project_task_states": coord.project_state_counts(
            snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"]
        ),
        "generated_at": cdata.utcnow().isoformat(),
    }


def _member_summary(users: list[dict], user_id: str | None) -> dict | None:
    for u in users:
        if u["id"] == user_id:
            return {"id": u["id"], "name": u["name"], "kind": u.get("kind") or "developer"}
    return None


def _recommendation_payload(
    db: Session, project_id: uuid.UUID, user_id: str | None, explain: bool
) -> dict:
    # Normalize once: requester.id arrives as a UUID object, but every loaded
    # row (and the pure service) compares string ids.
    uid = str(user_id) if user_id else None
    snap = cdata.load_snapshot(db, project_id)
    rejected = coord.rejected_task_ids_from_events(snap["events"], uid, cdata.utcnow())
    rec = coord.recommend_for_user(
        uid,
        snap["tasks"],
        snap["dep_edges"],
        snap["open_blocker_task_ids"],
        {u["id"]: u for u in snap["users"]},
        rejected,
        cdata.utcnow(),
    )

    # Cross-owner dependency awareness (grounded in task_dependencies + owner_id).
    cross_owner = coord.cross_owner_dependencies(snap["tasks"], snap["dep_edges"], {u["id"]: u for u in snap["users"]})
    # Contract collisions: two open tasks on the same method+route.
    collisions = coord.detect_contract_collisions(snap["tasks"], snap["contracts"])

    rec_out = rec.get("recommendation")
    if rec_out and explain:
        rec_out["explanation"] = reasoning.explain_recommendation(rec_out["task"]["title"], rec_out["reasons"])

    return {
        "project_id": str(project_id),
        "user": _member_summary(snap["users"], uid),
        **rec,
        "conflict_awareness": {
            "cross_owner_dependencies": cross_owner,
            "contract_collisions": collisions,
            "open_conflicts": snap["open_conflicts"],
        },
        "generated_at": cdata.utcnow().isoformat(),
    }


@router.get("/recommendations")
def get_recommendations(
    project_id: uuid.UUID,
    user_id: str | None = None,
    explain: bool = False,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """"What should I work on?" for one member (developer or agent). Pass
    ?user_id=<roster member> — agents call this with their own users-row id."""
    require_member(db, project_id, principal)
    requester = _require_member(db, project_id, user_id)
    return _recommendation_payload(db, project_id, requester.id if requester else None, explain)


@router.get("/recommendations/next")
def get_next_recommendation(
    project_id: uuid.UUID,
    user_id: str | None = None,
    explain: bool = False,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """The single best next move for the requester — the same deterministic
    engine as /recommendations, trimmed to the top recommendation."""
    require_member(db, project_id, principal)
    requester = _require_member(db, project_id, user_id)
    payload = _recommendation_payload(db, project_id, requester.id if requester else None, explain)
    return {
        "project_id": str(project_id),
        "user": payload["user"],
        "recommendation": payload["recommendation"],
        "alternates": payload["alternates"],
        "note": payload.get("note"),
        "generated_at": payload["generated_at"],
    }


class NextActionRequest(BaseModel):
    user_id: str | None = None


@router.post("/recommendations/next")
def post_next_action(
    project_id: uuid.UUID,
    body: NextActionRequest,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Project-level recommendation: "What should happen next in this project?"
    Deterministic precedence: resolve conflict > review > unblock > start.
    Optional user_id scopes the honest fallback note, nothing else."""
    require_member(db, project_id, principal)
    requester = _require_member(db, project_id, body.user_id)
    snap = cdata.load_snapshot(db, project_id)

    action = coord.project_next_action(
        snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"],
        snap["open_conflicts"], cdata.utcnow(),
    )
    counts = coord.project_state_counts(snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"])
    return {
        "project_id": str(project_id),
        "user": _member_summary(snap["users"], requester.id if requester else None),
        "action": action,
        "task_states": counts,
        "open_conflicts": len(snap["open_conflicts"]),
        "generated_at": cdata.utcnow().isoformat(),
    }


@router.get("/coordination")
def get_coordination_summary(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """One bounded payload for the dashboard's Next Actions panel: ready /
    blocked / needs review / conflicts / recommended next step. Deterministic
    only — safe to call on every dashboard load (no LLM here, ever)."""
    require_member(db, project_id, principal)
    snap = cdata.load_snapshot(db, project_id)

    incomplete = coord.incomplete_deps_per_task(snap["tasks"], snap["dep_edges"])
    downstream = coord.downstream_open_counts(snap["tasks"], snap["dep_edges"])
    users_by_id = {u["id"]: u for u in snap["users"]}

    ready = [t | {"dependency_count": snap["dependency_counts"].get(t["id"], 0)} for t in snap["tasks"]
             if coord.is_ready(t, snap["open_blocker_task_ids"], incomplete.get(t["id"], set()))]
    ranked = coord.rank_candidates(ready, None, downstream, cdata.utcnow(), incomplete, snap["open_blocker_task_ids"])
    ready_rows = _augment_ready_rows(ranked[:8], snap["dep_edges"], snap["dependency_counts"], snap["users"])

    tasks_by_id = {t["id"]: t for t in snap["tasks"]}
    blocked_rows = []
    for t in snap["tasks"]:
        state = coord.classify_task(t, snap["open_blocker_task_ids"], incomplete.get(t["id"], set()))
        if state not in (coord.BLOCKED, coord.WAITING):
            continue
        owner = users_by_id.get(t.get("owner_id") or "")
        waiting_on = [
            {"id": dep_id, "title": dep["title"], "status": dep["status"]}
            for dep_id in sorted(incomplete.get(t["id"], set()))
            if (dep := tasks_by_id.get(dep_id))
        ]
        manual = [
            b["description"] for b in snap["open_task_blockers"] if b["task_id"] == t["id"]
        ]
        blocked_rows.append(
            {
                "id": t["id"],
                "title": t["title"],
                "state": state,
                "priority": t.get("priority") or "medium",
                "owner_id": t.get("owner_id"),
                "owner_name": owner["name"] if owner else None,
                "waiting_on": waiting_on,
                "manual_blocker": manual[0] if manual else None,
            }
        )

    review_rows = [
        {
            "id": t["id"],
            "title": t["title"],
            "owner_id": t.get("owner_id"),
            "owner_name": (users_by_id.get(t.get("owner_id") or "") or {}).get("name"),
            "created_at": t.get("created_at"),
        }
        for t in snap["tasks"]
        if t.get("status") == "review"
    ]

    action = coord.project_next_action(
        snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"],
        snap["open_conflicts"], cdata.utcnow(),
    )
    overlaps = coord.detect_task_overlaps(snap["tasks"])
    collisions = coord.detect_contract_collisions(snap["tasks"], snap["contracts"])
    cross_owner = coord.cross_owner_dependencies(snap["tasks"], snap["dep_edges"], users_by_id)

    return {
        "project_id": str(project_id),
        "task_states": coord.project_state_counts(snap["tasks"], snap["dep_edges"], snap["open_blocker_task_ids"]),
        "ready_to_start": ready_rows,
        "blocked": blocked_rows,
        "needs_review": review_rows,
        "conflicts": {
            "open_contract_conflicts": snap["open_conflicts"],
            "task_overlaps": overlaps,
            "contract_collisions": collisions,
            "cross_owner_dependencies": cross_owner,
        },
        "recommended_next_step": action,
        "who_is_doing_what": coord.who_is_doing_what(snap["tasks"], snap["users"]),
        "generated_at": cdata.utcnow().isoformat(),
    }


class RecommendationDecision(BaseModel):
    user_id: str = Field(description="member (or agent) making the decision")
    note: str | None = Field(default=None, max_length=500)


def _require_task(db: Session, project_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = db.scalar(
        select(Task).where(Task.id == task_id, Task.project_id == project_id)
    )
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/tasks/{task_id}/accept-recommendation")
def accept_recommendation(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: RecommendationDecision,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """    Human override — ACCEPT. Claims the recommended task for the deciding
    member (assigns tasks.owner_id — the existing frozen assignment mechanism;
    no new assignment system) and writes a `recommendation_accepted` event.
    Scaffold never takes ownership on its own: only an explicit POST does this.
    Re-accepting the same task by the same member is idempotent (200, nothing
    changes); accepting a task owned by someone else reassigns it (also 200 —
    a human decision wins over a previous assignment)."""
    require_member(db, project_id, principal)
    task = _require_task(db, project_id, task_id)
    member = _require_member(db, project_id, body.user_id)

    if task.status == "done":
        raise HTTPException(status_code=400, detail="task is already done")

    already_mine = task.owner_id == member.id
    status_code = 200 if already_mine else 201
    if not already_mine:
        task.owner_id = member.id

    db.add(
        Event(
            project_id=project_id,
            type="recommendation_accepted",
            payload={"task_id": str(task_id), "user_id": str(member.id), "title": task.title},
        )
    )
    if not already_mine:
        db.add(
            Event(
                project_id=project_id,
                type="task_assigned",
                payload={"task_id": str(task_id), "title": task.title, "owner_id": str(member.id)},
            )
        )
    db.commit()

    return JSONResponse(
        status_code=status_code,
        content={
            "accepted": True,
            "task_id": str(task_id),
            "task": _task_out(db, task),
            "owner_id": str(member.id),
            "idempotent": already_mine,
        },
    )


@router.post("/tasks/{task_id}/reject-recommendation", status_code=200)
def reject_recommendation(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: RecommendationDecision,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> dict:
    """Human override — REJECT. Records the decision as an event; the
    deterministic recommender excludes this task for this member for 7 days
    (services/coordination.rejected_task_ids_from_events). Nothing else
    changes — no status flip, no assignment, fully reversible by accepting."""
    require_member(db, project_id, principal)
    task = _require_task(db, project_id, task_id)
    member = _require_member(db, project_id, body.user_id)

    db.add(
        Event(
            project_id=project_id,
            type="recommendation_rejected",
            payload={
                "task_id": str(task_id),
                "user_id": str(member.id),
                "title": task.title,
                "note": body.note,
            },
        )
    )
    db.commit()
    return {"rejected": True, "task_id": str(task_id), "user_id": str(member.id)}

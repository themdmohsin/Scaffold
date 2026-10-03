"""Phase 3 verification: team collaboration (members, agent identity, ownership).

Run from engine/:  python -m tests.test_phase3
No network, no LLM: the pure `services/team.py` section runs anywhere. The
route sections hit the real DB exactly like the Day 2/3/4b/5 suites and are
SKIPPED (loudly) when DATABASE_URL is not set.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"

import tests.auth_helper as auth  # noqa: E402  (sets SUPABASE_JWT_SECRET before app.config)

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# 1. Pure team-service units — no DB, no clock reads
# ---------------------------------------------------------------------------
print("\n== team service: activity status (deterministic, no LLM) ==")
from app.services import team as team_service  # noqa: E402

check("recent activity -> ACTIVE", team_service.compute_status(NOW, NOW - timedelta(minutes=5), False) == "ACTIVE")
check("stale-but-recent activity -> IDLE", team_service.compute_status(NOW, NOW - timedelta(hours=2), False) == "IDLE")
check("very old activity -> OFFLINE", team_service.compute_status(NOW, NOW - timedelta(days=3), False) == "OFFLINE")
check("never seen -> OFFLINE", team_service.compute_status(NOW, None, False) == "OFFLINE")
check("blocker beats recency -> BLOCKED", team_service.compute_status(NOW, NOW, True) == "BLOCKED")

print("\n== team service: last activity + current task (pure) ==")
tasks = [
    {"id": "t1", "owner_id": "u1", "status": "in_progress", "title": "older", "created_at": NOW - timedelta(hours=5)},
    {"id": "t2", "owner_id": "u1", "status": "todo", "title": "newer", "created_at": NOW - timedelta(hours=1)},
    {"id": "t3", "owner_id": "u1", "status": "done", "title": "done one", "created_at": NOW},
    {"id": "t4", "owner_id": "u2", "status": "todo", "title": "someone else's", "created_at": NOW},
]
decisions = [{"made_by": "u1", "created_at": NOW - timedelta(hours=10)}]
events = [{"payload": {"user_id": "u1"}, "created_at": NOW - timedelta(minutes=2)}]

cur = team_service.compute_current_task("u1", tasks)
check("current task is the most recent OPEN task, not done", cur == {"id": "t2", "status": "todo", "title": "newer"}, str(cur))
check("no open tasks -> current_task None", team_service.compute_current_task("u2", [tasks[3] | {"status": "done"}]) is None)

last = team_service.compute_last_activity("u1", tasks, decisions, events)
# t3 (done task, owner u1) was created at NOW itself — any task row (done or
# not) counts as activity, so NOW is the true max, ahead of the -2min event.
check("last activity picks the newest of task/decision/event timestamps", last == NOW, str(last))
check("unknown user has no activity", team_service.compute_last_activity("u-ghost", tasks, decisions, events) is None)

blocked_ids = {"t2"}
check("blocked open task on this user -> is_blocked True", team_service.is_blocked("u1", tasks, blocked_ids) is True)
check("blocked task belonging to someone else -> False", team_service.is_blocked("u2", tasks, blocked_ids) is False)

row = team_service.build_member_row(
    {"id": "u1", "name": "Alice", "role": "backend", "kind": "developer", "membership_status": "active", "joined_at": NOW},
    NOW,
    tasks,
    decisions,
    events,
    blocked_ids,
)
check("member row surfaces BLOCKED status when their current task is blocked", row["activity_status"] == "BLOCKED", str(row))
check("member row carries current_task", row["current_task"]["id"] == "t2")

print("\n== team service: agent identity is provider-agnostic ==")
agent_row = team_service.build_member_row(
    {
        "id": "a1",
        "name": "OpenCode Agent A",
        "role": "agent",
        "kind": "agent",
        "agent_provider": "anthropic",
        "agent_model": "claude-sonnet-5",
        "membership_status": "active",
        "joined_at": NOW,
    },
    NOW,
    [],
    [],
    [],
    set(),
)
check("agent row keeps provider/model as free text (no hardcoded vendor)", agent_row["agent_provider"] == "anthropic" and agent_row["agent_model"] == "claude-sonnet-5")
check("agent with no activity yet -> OFFLINE, not faked ACTIVE", agent_row["activity_status"] == "OFFLINE")

# ---------------------------------------------------------------------------
# 2. DB-backed route tests — skipped loudly without DATABASE_URL
# ---------------------------------------------------------------------------
print("\n== routes against the real DB (skipped when no engine/.env) ==")
from app.config import settings  # noqa: E402

db_url = (settings.database_url or "").strip()
if not db_url:
    print("  SKIP  DATABASE_URL not set on this machine — run on a machine with engine/.env")
else:
    import sqlalchemy as sa  # noqa: E402
    from starlette.testclient import TestClient  # noqa: E402

    from app.db.session import _init  # noqa: E402
    from app.main import app  # noqa: E402

    client = TestClient(app, raise_server_exceptions=False)
    # Phase 6: identity comes from the Bearer token; POST /projects makes the
    # CALLER the owner, so the client's default identity is the project owner.
    client.headers.update(auth.auth_headers(auth.jwt_for(auth.OWNER_SUB)))
    SessionLocal = _init()

    r = client.post("/projects", json={"name": "phase3-test-team-collab"})
    check("project created for the phase3 route pass", r.status_code == 201, r.text)
    pid = uuid.UUID(r.json()["id"])
    db = SessionLocal()
    try:
        # --- existing Day 5 invite/join still works untouched -----------------
        r = client.post(f"/projects/{pid}/invite", json={})
        check("Day 5 invite endpoint untouched", r.status_code == 201, r.text)
        code = r.json()["code"]
        mohsin_jwt, _mohsin_sub = auth.extra_member("Mohsin")
        r = client.post(
            "/projects/join",
            json={"code": code, "name": "Mohsin", "role": "backend"},
            headers=auth.auth_headers(mohsin_jwt),
        )
        check("Day 5 join endpoint untouched (identity now from the token)", r.status_code == 201, r.text)
        mohsin_id = r.json()["user_id"]

        # --- members roster ----------------------------------------------------
        r = client.get(f"/projects/{pid}/members")
        check("GET members 200", r.status_code == 200, r.text)
        members = r.json()
        check("joined developer appears with kind=developer", any(m["id"] == mohsin_id and m["kind"] == "developer" for m in members), str(members))
        check("member has activity_status one of the four buckets", all(m["activity_status"] in ("ACTIVE", "IDLE", "BLOCKED", "OFFLINE") for m in members))

        # --- agent registration (upsert) ---------------------------------------
        r = client.post(f"/projects/{pid}/agents", json={"name": "Agent X", "provider": "google", "model": "gemini-2.5-flash"})
        check("register agent 201", r.status_code == 201, r.text)
        agent_id = r.json()["id"]
        check("agent registration is_new true first time", r.json()["is_new"] is True)
        r = client.post(f"/projects/{pid}/agents", json={"name": "Agent X", "provider": "google", "model": "gemini-3.0"})
        check("re-registering the same agent name upserts (no dup)", r.status_code == 201 and r.json()["is_new"] is False, r.text)
        check("upsert updates the model", r.json()["agent_model"] == "gemini-3.0", r.text)

        n_agents = db.execute(
            sa.text("SELECT count(*) FROM users WHERE project_id=:p AND kind='agent'"), {"p": pid}
        ).scalar()
        check("upsert did not create a duplicate agent user row", n_agents == 1, str(n_agents))

        r = client.get(f"/projects/{pid}/members")
        members = r.json()
        check("agent appears in the roster with kind=agent", any(m["id"] == agent_id and m["kind"] == "agent" for m in members), str(members))

        # --- task assignment reuses the existing task system (no 2nd system) ---
        r = client.post(f"/projects/{pid}/tasks", json={"title": "API integration", "owner_id": agent_id})
        check("task assignable to an agent via existing owner_id field", r.status_code == 201, r.text)
        task_id = r.json()["id"]
        r = client.get(f"/projects/{pid}/members")
        agent_row = next(m for m in r.json() if m["id"] == agent_id)
        check("agent's current_task reflects the assigned task", agent_row["current_task"] is not None and agent_row["current_task"]["id"] == task_id, str(agent_row))

        # --- ownership: gate, transfer ------------------------------------------
        # The creator already owns the project (POST /projects), so the FIRST
        # transfer is owner-gated; after it the creator is demoted to member.
        r = client.post(f"/projects/{pid}/owner", json={"user_id": mohsin_id})
        check("owner transfers ownership to a member", r.status_code == 200 and r.json()["owner_user_id"] == mohsin_id, r.text)

        r = client.patch(f"/projects/{pid}/members/{agent_id}", json={"role": "member"})
        check("admin-gated PATCH rejects the demoted (plain member) caller", r.status_code == 403, r.text)

        r = client.patch(
            f"/projects/{pid}/members/{agent_id}",
            json={"role": "member", "requesting_user_id": mohsin_id},
            headers=auth.auth_headers(mohsin_jwt),
        )
        check(
            "admin-gated PATCH succeeds for the new owner (legacy requesting_user_id ignored)",
            r.status_code == 200 and r.json()["role"] == "member",
            r.text,
        )

        r = client.post(f"/projects/{pid}/owner", json={"user_id": agent_id})
        check("non-owner cannot silently steal ownership", r.status_code == 403, r.text)

        r = client.post(
            f"/projects/{pid}/owner",
            json={"user_id": agent_id, "requesting_user_id": mohsin_id},
            headers=auth.auth_headers(mohsin_jwt),
        )
        check("current owner CAN transfer ownership", r.status_code == 200 and r.json()["owner_user_id"] == agent_id, r.text)
        check(
            "the agent's account now carries the owner role (agent is account-backed)",
            auth.role_of(str(pid), auth.OWNER_SUB) == "owner",
            str(auth.role_of(str(pid), auth.OWNER_SUB)),
        )

        # --- removal: owner protected, membership soft-deleted ------------------
        r = client.delete(f"/projects/{pid}/members/{agent_id}?requesting_user_id={agent_id}")
        check("cannot remove the current project owner", r.status_code == 400, r.text)

        r = client.delete(f"/projects/{pid}/members/{mohsin_id}?requesting_user_id={agent_id}")
        check("owner can remove a non-owner member", r.status_code == 200 and r.json()["membership_status"] == "removed", r.text)

        r = client.get(f"/projects/{pid}/members")
        check("removed member no longer appears in the roster", not any(m["id"] == mohsin_id for m in r.json()), str(r.json()))

        n_events = db.execute(
            sa.text("SELECT count(*) FROM events WHERE project_id=:p AND type IN ('agent_registered','agent_session_updated','member_role_changed','member_removed','project_owner_set')"),
            {"p": pid},
        ).scalar()
        check("team events reuse the existing events table (no 2nd activity log)", n_events >= 5, str(n_events))

        # --- unknown project / member 404s --------------------------------------
        r = client.get(f"/projects/{uuid.uuid4()}/members")
        check("unknown project 404s on members", r.status_code == 404)
        r = client.patch(f"/projects/{pid}/members/{uuid.uuid4()}", json={"role": "member"})
        check("unknown member 404s on patch", r.status_code == 404)
    finally:
        try:
            db.execute(sa.text("DELETE FROM events WHERE project_id = :p"), {"p": pid})
            db.execute(sa.text("UPDATE projects SET owner_user_id = NULL WHERE id = :p"), {"p": pid})
            db.execute(sa.text("DELETE FROM tasks WHERE project_id = :p"), {"p": pid})
            db.execute(sa.text("DELETE FROM users WHERE project_id = :p"), {"p": pid})
            db.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": pid})
            db.commit()
        except Exception:  # noqa: BLE001 — cleanup must never mask test results
            db.rollback()
        db.close()

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)

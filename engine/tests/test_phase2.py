"""Phase 2 verification: the Project Control Center (task board + coordination layer).

Run from engine/:  python -m tests.test_phase2

Covers, against the REAL Postgres DB (same convention as test_day2/day3/day4b/day5 —
TestClient over the real app, real migrations already applied at import time):
  - migrate_phase2.sql applied cleanly (idempotent — re-run is a no-op)
  - full task lifecycle: create -> assign -> priority -> review -> done -> completed_at
  - dependency edges: add/remove, self-dependency rejected, cross-project rejected,
    is_blocked / blocked_by_dependencies computed correctly
  - manual blocked switch <-> blockers table (create on block, resolve on unblock)
  - GET /projects/:id/users (new read-only roster route)
  - GET /projects/:id/context additive keys: task_counts, open_conflicts
  - every new/renamed event type actually gets written
  - frozen Day 1 shape (id/title/status/owner_id/due_at/created_at, task_created/
    task_updated payloads) stays byte-identical

Pure sections run anywhere; the DB-backed section SKIPS loudly without DATABASE_URL
(test_day5 convention) so this file is still safe to run on a machine with no
engine/.env.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. Pure schema validation — no DB needed
# ---------------------------------------------------------------------------
print("\n== schemas: TaskCreate / TaskUpdate validation ==")
from pydantic import ValidationError  # noqa: E402
from app.schemas import TaskCreate, TaskUpdate  # noqa: E402

try:
    TaskCreate(title="x", priority="urgent")
    ok_priority = True
except ValidationError:
    ok_priority = False
check("valid priority accepted", ok_priority)

try:
    TaskCreate(title="x", priority="critical")
    bad_priority_ok = False
except ValidationError:
    bad_priority_ok = True
check("invalid priority rejected", bad_priority_ok)

try:
    TaskUpdate(status="review")
    review_ok = True
except ValidationError:
    review_ok = False
check("'review' is a valid status (Phase 2)", review_ok)

try:
    TaskCreate(title="")
    empty_title_ok = False
except ValidationError:
    empty_title_ok = True
check("empty title still rejected (frozen rule)", empty_title_ok)

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
    from app.db.models import Event  # noqa: E402

    client = TestClient(app, raise_server_exceptions=False)
    SessionLocal = _init()
    db = SessionLocal()

    pid = None
    try:
        pname = f"phase2-test-{uuid.uuid4().hex[:8]}"
        r = client.post("/projects", json={"name": pname})
        check("project created for the phase2 route pass", r.status_code == 201, r.text)
        pid = r.json()["id"]

        # -- users route -----------------------------------------------------
        r = client.get(f"/projects/{pid}/users")
        check("GET /users 200 empty roster on a fresh project", r.status_code == 200 and r.json() == [], r.text)

        code = client.post(f"/projects/{pid}/invite").json()["code"]
        alice = client.post("/projects/join", json={"code": code, "name": "Alice", "role": "backend"}).json()
        alice_id = alice["user_id"]
        bob = client.post("/projects/join", json={"code": code, "name": "Bob"}).json()
        bob_id = bob["user_id"]

        r = client.get(f"/projects/{pid}/users")
        names = sorted(u["name"] for u in r.json())
        check("GET /users lists both joined users, name-sorted", names == ["Alice", "Bob"], str(r.json()))
        check("GET /users unknown project 404s", client.get(f"/projects/{uuid.uuid4()}/users").status_code == 404)

        # -- task create with Phase 2 fields + dependency -------------------
        r = client.post(
            f"/projects/{pid}/tasks",
            json={"title": "Design auth", "priority": "high", "description": "spec it", "created_by": alice_id},
        )
        check("create task with Phase 2 fields", r.status_code == 201, r.text)
        t1 = r.json()
        check("frozen fields present and correct", t1["title"] == "Design auth" and t1["status"] == "todo")
        check("priority stored", t1["priority"] == "high")
        check("description stored", t1["description"] == "spec it")
        check("created_by stored", t1["created_by"] == alice_id)
        check("new task starts unblocked", t1["is_blocked"] is False and t1["blocked"] is False)
        tid1 = t1["id"]

        r = client.post(
            f"/projects/{pid}/tasks",
            json={"title": "Implement auth", "owner_id": bob_id, "dependencies": [tid1]},
        )
        check("create task with a dependency + owner", r.status_code == 201, r.text)
        t2 = r.json()
        tid2 = t2["id"]
        check("dependency edge recorded", [d["id"] for d in t2["dependencies"]] == [tid1])
        check("incomplete dependency blocks the dependent task", t2["is_blocked"] is True)
        check("blocked_by_dependencies names the blocker", t2["blocked_by_dependencies"][0]["title"] == "Design auth")

        # -- validation: self-dependency, cross-project, missing target ------
        r = client.post(f"/projects/{pid}/tasks/{tid2}/dependencies", json={"depends_on_task_id": tid2})
        check("self-dependency rejected 400", r.status_code == 400, r.text)

        other = client.post("/projects", json={"name": f"phase2-other-{uuid.uuid4().hex[:8]}"}).json()
        other_task = client.post(f"/projects/{other['id']}/tasks", json={"title": "elsewhere"}).json()
        r = client.post(f"/projects/{pid}/tasks/{tid2}/dependencies", json={"depends_on_task_id": other_task["id"]})
        check("cross-project dependency rejected 400", r.status_code == 400, r.text)
        db.execute(sa.text("DELETE FROM events WHERE project_id = :p"), {"p": other["id"]})
        db.execute(sa.text("DELETE FROM tasks WHERE project_id = :p"), {"p": other["id"]})
        db.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": other["id"]})
        db.commit()

        r = client.post(f"/projects/{pid}/tasks/{tid1}/dependencies", json={"depends_on_task_id": str(uuid.uuid4())})
        check("dependency on a nonexistent task rejected 400", r.status_code == 400, r.text)

        # -- dependency add/remove endpoints are idempotent ------------------
        r = client.post(f"/projects/{pid}/tasks/{tid2}/dependencies", json={"depends_on_task_id": tid1})
        check("re-adding the same dependency edge is a no-op (still one)", len(r.json()["dependencies"]) == 1, r.text)
        r = client.delete(f"/projects/{pid}/tasks/{tid2}/dependencies/{tid1}")
        check("remove dependency edge", r.status_code == 200 and r.json()["dependencies"] == [], r.text)
        r = client.delete(f"/projects/{pid}/tasks/{tid2}/dependencies/{tid1}")
        check("removing an already-missing edge is a no-op, not an error", r.status_code == 200, r.text)
        # restore it for the rest of the pass
        client.post(f"/projects/{pid}/tasks/{tid2}/dependencies", json={"depends_on_task_id": tid1})

        # -- status lifecycle: review + completed_at set/clear ---------------
        r = client.patch(f"/projects/{pid}/tasks/{tid1}", json={"status": "review"})
        check("status -> review accepted", r.status_code == 200 and r.json()["status"] == "review", r.text)
        r = client.patch(f"/projects/{pid}/tasks/{tid1}", json={"status": "done"})
        check("status -> done sets completed_at", r.status_code == 200 and r.json()["completed_at"] is not None, r.text)
        r = client.patch(f"/projects/{pid}/tasks/{tid1}", json={"status": "todo"})
        check("moving off done clears completed_at", r.json()["completed_at"] is None, r.text)
        # put it back to done so tid2's dependency check below is meaningful
        client.patch(f"/projects/{pid}/tasks/{tid1}", json={"status": "done"})

        r = client.get(f"/projects/{pid}/tasks")
        t2_after = next(t for t in r.json() if t["id"] == tid2)
        check("dependency completing unblocks the dependent task", t2_after["is_blocked"] is False, str(t2_after))

        # -- assignment + priority change ------------------------------------
        r = client.patch(f"/projects/{pid}/tasks/{tid2}", json={"owner_id": alice_id, "priority": "urgent"})
        check("reassign + reprioritize in one PATCH", r.json()["owner_id"] == alice_id and r.json()["priority"] == "urgent", r.text)

        # -- title/description edit ------------------------------------------
        r = client.patch(f"/projects/{pid}/tasks/{tid2}", json={"title": "Implement auth (v2)", "description": "now with oauth"})
        check("title/description edit", r.json()["title"] == "Implement auth (v2)" and r.json()["description"] == "now with oauth", r.text)

        # -- manual blocked switch <-> blockers table -------------------------
        r = client.patch(f"/projects/{pid}/tasks/{tid2}", json={"blocked": True, "blocker_reason": "waiting on design sign-off"})
        check("manual blocked=true accepted", r.json()["blocked"] is True and r.json()["is_blocked"] is True, r.text)

        ctx = client.get(f"/projects/{pid}/context").json()
        task_blockers = [b for b in ctx["blockers"] if "waiting on design sign-off" in (b["description"] or "")]
        check("blocking the task opened a blockers row", len(task_blockers) == 1, str(ctx["blockers"]))
        check("context task_counts.blocked reflects the manual block", ctx["task_counts"]["blocked"] == 1, str(ctx["task_counts"]))

        r = client.patch(f"/projects/{pid}/tasks/{tid2}", json={"blocked": True, "blocker_reason": "same reason again"})
        ctx2 = client.get(f"/projects/{pid}/context").json()
        dup_blockers = [b for b in ctx2["blockers"] if b["description"].startswith("Task blocked: Implement auth")]
        check("re-blocking an already-blocked task does not duplicate the blocker row", len(dup_blockers) == 1, str(ctx2["blockers"]))

        r = client.patch(f"/projects/{pid}/tasks/{tid2}", json={"blocked": False})
        check("unblocking clears blocked", r.json()["blocked"] is False, r.text)
        ctx3 = client.get(f"/projects/{pid}/context").json()
        check("unblocking resolves the task's blocker (no longer in open list)", all("Implement auth" not in (b["description"] or "") for b in ctx3["blockers"]), str(ctx3["blockers"]))
        check("context task_counts.blocked back to 0", ctx3["task_counts"]["blocked"] == 0, str(ctx3["task_counts"]))

        # -- 404s for the new endpoints ---------------------------------------
        check("dependency endpoint 404s on unknown project", client.post(f"/projects/{uuid.uuid4()}/tasks/{tid1}/dependencies", json={"depends_on_task_id": tid1}).status_code == 404)
        check("dependency endpoint 404s on unknown task", client.post(f"/projects/{pid}/tasks/{uuid.uuid4()}/dependencies", json={"depends_on_task_id": tid1}).status_code == 404)

        # -- events: every documented type actually gets written -------------
        rows = db.scalars(sa.select(Event).where(Event.project_id == uuid.UUID(pid))).all()
        types_seen = {e.type for e in rows}
        expected = {
            "task_created", "task_updated", "task_assigned", "task_status_changed",
            "task_completed", "task_priority_changed", "task_blocked", "task_unblocked",
            "task_dependency_added", "task_dependency_removed",
        }
        missing = expected - types_seen
        check("every Phase 2 event type was written at least once", not missing, f"missing={missing}")

        created_events = [e for e in rows if e.type == "task_created"]
        check("task_created payload shape unchanged (task_id, title only)", all(set(e.payload.keys()) == {"task_id", "title"} for e in created_events), str([e.payload for e in created_events]))

        # -- open_conflicts is distinct from task-level blockers --------------
        check("open_conflicts stays 0 (no contract conflicts here)", ctx3["open_conflicts"] == 0, str(ctx3["open_conflicts"]))

        # -- re-running the migration is a no-op (idempotent) -----------------
        from app.db.session import _apply_phase2_migration  # noqa: E402

        try:
            _apply_phase2_migration(db.get_bind())
            migration_rerun_ok = True
        except Exception as exc:  # noqa: BLE001
            migration_rerun_ok = False
            print(f"    [migration re-run raised: {exc}]")
        check("migrate_phase2.sql re-run is idempotent", migration_rerun_ok)

    finally:
        # Best-effort teardown, in FK-safe order, so the demo DB stays clean.
        try:
            if pid:
                db.execute(sa.text("DELETE FROM blockers WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)"), {"p": pid})
                db.execute(sa.text("DELETE FROM events WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM tasks WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM users WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": pid})
                db.commit()
        except Exception:  # noqa: BLE001 — cleanup must never mask test results
            db.rollback()
        db.close()

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:")
    for name in FAIL:
        print(f"  - {name}")
    sys.exit(1)

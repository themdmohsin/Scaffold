"""Phase 4 verification: intelligent coordination.

Run from engine/:  python -m tests.test_phase4

Two layers, same convention as test_phase2/3:
  1. PURE units (no DB, no clock reads — `now` is passed explicitly) over
     services/coordination.py: state classification, ready detection, the
     explainable scorer, the recommendation pipeline, overlap detection,
     cross-owner awareness, project next action, rejection memory.
  2. DB-backed route legs against the REAL database (skipped loudly without
     DATABASE_URL, like every suite in this repo): full demo lifecycle —
     project → members + agents → tasks (done/in_progress/review/blocked/
     waiting/ready) → ready endpoint → recommendations per member → agent
     recommendation → accept/reject overrides → authorization checks →
     coordination summary → MCP tools over the real wire.

Also pins the frozen contracts: Phase 1-3 routes byte-identical (spot-checked
by the regression suites this file tells you to run afterwards).
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

import tests.auth_helper as auth  # noqa: E402  (sets SUPABASE_JWT_SECRET before app.config)

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
AGENT = "33333333-3333-3333-3333-333333333333"

# ---------------------------------------------------------------------------
# 1. Pure units — state classification + ready detection
# ---------------------------------------------------------------------------
print("\n== pure: state classification + ready detection ==")
from app.services import coordination as coord  # noqa: E402

def task(id="t1", status="todo", blocked=False, **kw):
    return {"id": id, "title": kw.pop("title", id), "status": status, "blocked": blocked,
            "priority": "medium", "owner_id": None, "created_at": NOW.isoformat(), **kw}

check("todo, no blockers, no deps -> READY", coord.classify_task(task(), set(), set()) == coord.READY)
check("done -> DONE", coord.classify_task(task(status="done"), set(), set()) == coord.DONE)
check("review -> REVIEW", coord.classify_task(task(status="review"), set(), set()) == coord.REVIEW)
check("in_progress -> IN_PROGRESS", coord.classify_task(task(status="in_progress"), set(), set()) == coord.IN_PROGRESS)
check("manual blocked -> BLOCKED (not READY)", coord.classify_task(task(blocked=True), set(), set()) == coord.BLOCKED)
check("open blocker row -> BLOCKED", coord.classify_task(task(), {"t1"}, set()) == coord.BLOCKED)
check("in_progress with open blocker -> BLOCKED (blockers win for visibility)",
      coord.classify_task(task(status="in_progress"), {"t1"}, set()) == coord.BLOCKED)
check("todo with incomplete dependency -> WAITING_ON_DEPENDENCY",
      coord.classify_task(task(), set(), {"dep1"}) == coord.WAITING)
check("in_progress with incomplete deps stays IN_PROGRESS (work began)",
      coord.classify_task(task(status="in_progress"), set(), {"dep1"}) == coord.IN_PROGRESS)
check("is_ready false when blocked", coord.is_ready(task(blocked=True), set(), set()) is False)
check("is_ready false when deps incomplete", coord.is_ready(task(), set(), {"d"}) is False)
check("is_ready true for unassigned ready task (claimable)", coord.is_ready(task(), set(), set()) is True)

# ---------------------------------------------------------------------------
# 2. Pure units — dependency graph helpers
# ---------------------------------------------------------------------------
print("\n== pure: dependency graph ==")
tasks = [
    task("dep", status="done", title="done dep"),
    task("dep-open", status="todo", title="open dep"),
    task("child", title="child", owner_id=BOB),
    task("grand", status="in_progress", title="grand", owner_id=ALICE),
]
edges = [("child", "dep"), ("child", "dep-open"), ("grand", "child")]
inc = coord.incomplete_deps_per_task(tasks, edges)
check("incomplete_deps: done dep excluded", "dep" not in inc.get("child", set()))
check("incomplete_deps: open dep included", "dep-open" in inc.get("child", set()))
check("incomplete_deps: dependent-of-dependent counted", inc.get("grand") == {"child"})
down = coord.downstream_open_counts(tasks, edges)
check("downstream counts open dependents only", down.get("dep") == 1 and down.get("dep-open") == 1 and down.get("child") == 1)

users = [{"id": ALICE, "name": "Alice", "kind": "developer"}, {"id": BOB, "name": "Bob", "kind": "agent"}]
cross = coord.cross_owner_dependencies(tasks, edges, {u["id"]: u for u in users})
check("cross-owner edges detected (both ends owned, different owners)", len(cross) == 1, str(cross))
check("cross-owner names grounded in users rows",
      cross and cross[0]["waiting_owner"] == "Alice" and cross[0]["blocking_owner"] == "Bob", str(cross))
check("no cross-owner row when one end is unowned",
      coord.cross_owner_dependencies([t | {"owner_id": None} for t in tasks], edges, {u["id"]: u for u in users}) == [])

# ---------------------------------------------------------------------------
# 3. Pure units — explainable scorer
# ---------------------------------------------------------------------------
print("\n== pure: explainable scorer ==")
t_assigned = task("a", owner_id=ALICE, priority="urgent", title="Implement payment service")
score, factors, reasons = coord.score_candidate(t_assigned, ALICE, 3, NOW)
check("urgent priority scores 40", factors.get("priority") == 40, str(factors))
check("assignment bonus applied for the assignee", factors.get("assignment") == 25, str(factors))
check("downstream bonus 3*4=12", factors.get("downstream") == 12, str(factors))
check("total = sum of factors", score == sum(factors.values()))
check("reasons are human-readable and mention assignment", any("assigned to you" in r for r in reasons), str(reasons))
check("reasons mention downstream impact", any("downstream" in r for r in reasons), str(reasons))

score_other, factors_other, _ = coord.score_candidate(t_assigned, BOB, 3, NOW)
check("no assignment bonus for a different user", "assignment" not in factors_other)
check("assignment never leaks across users", score_other < score)

t_due = task("d", due_at=(NOW + timedelta(hours=24)).isoformat())
_, f_due, r_due = coord.score_candidate(t_due, None, 0, NOW)
check("due within 48h scores deadline bonus", f_due.get("deadline") == 10, str(f_due))
check("deadline reason surfaces", any("48 hours" in r for r in r_due), str(r_due))
t_old = task("old", created_at=(NOW - timedelta(days=10)).isoformat())
_, f_old, r_old = coord.score_candidate(t_old, None, 0, NOW)
check("age bonus after a week open", f_old.get("age") == 2, str(f_old))
_, f_cap, _ = coord.score_candidate(task("cap"), None, 50, NOW)
check("downstream bonus capped at 20", f_cap.get("downstream") == 20, str(f_cap))

# ranking: total order deterministic, higher score first
r1, r2 = t_assigned, task("b", priority="low")
ranked = coord.rank_candidates([r2, r1], ALICE, {"a": 0, "b": 0}, NOW, {}, set())
check("ranker puts higher score first", ranked[0]["id"] == "a")
check("every ranked row carries score_factors", all("score_factors" in r and "reasons" in r for r in ranked))

# ---------------------------------------------------------------------------
# 4. Pure units — "what should I work on?" pipeline
# ---------------------------------------------------------------------------
print("\n== pure: recommendation pipeline ==")
def setup_world():
    ts = [
        task("done1", status="done", title="Design auth"),
        task("prog1", status="in_progress", owner_id=ALICE, title="Alice current work"),
        task("ready-mine", owner_id=ALICE, priority="high", title="My ready task"),
        task("ready-other", owner_id=BOB, title="Bob's task"),
        task("ready-free", priority="urgent", title="Free ready task"),
        task("waiting-mine", owner_id=ALICE, title="Alice blocked task"),
    ]
    es = [("waiting-mine", "ready-free")]
    return ts, es

ts, es = setup_world()
inc = coord.incomplete_deps_per_task(ts, es)
bl = coord.downstream_open_counts(ts, es)
rec = coord.recommend_for_user(ALICE, ts, es, set(), {u["id"]: u for u in users}, set(), NOW)
check("prefers own ready work over anything else",
      rec["recommendation"]["task"]["id"] == "ready-mine", str(rec["recommendation"]))
check("own ready recommendation explains assignment",
      any("assigned to you" in r for r in rec["recommendation"]["reasons"]), str(rec["recommendation"]["reasons"]))
check("current_work reflects in_progress tasks", rec["current_work"][0]["id"] == "prog1")

ts2, es2 = setup_world()
ts2 = [t for t in ts2 if t["id"] != "ready-mine"]
rec2 = coord.recommend_for_user(ALICE, ts2, es2, set(), {u["id"]: u for u in users}, set(), NOW)
check("with no own ready work, suggests continuing in_progress",
      rec2["recommendation"]["task"]["id"] == "prog1"
      and rec2["recommendation"]["state"] == coord.IN_PROGRESS, str(rec2["recommendation"]))

ts3, es3 = setup_world()
ts3 = [t for t in ts3 if t["id"] not in ("ready-mine", "prog1")]
rec3 = coord.recommend_for_user(ALICE, ts3, es3, set(), {u["id"]: u for u in users}, set(), NOW)
check("blocked own task routes to the ready dependency that unblocks it",
      rec3["recommendation"]["task"]["id"] == "ready-free", str(rec3["recommendation"]))
check("unblocking reason names the waiting task",
      any("unblocks your task" in r for r in rec3["recommendation"]["reasons"]), str(rec3["recommendation"]["reasons"]))

ts4, es4 = setup_world()
ts4 = [t for t in ts4 if t.get("owner_id") != ALICE and t["id"] != "ready-other"]
rec4 = coord.recommend_for_user(ALICE, ts4, es4, set(), {u["id"]: u for u in users}, set(), NOW)
check("with nothing of my own, suggests claimable unassigned work",
      rec4["recommendation"]["task"]["id"] == "ready-free", str(rec4["recommendation"]))
check("claimable recommendation says free to claim",
      any("claim" in r for r in rec4["recommendation"]["reasons"]), str(rec4["recommendation"]["reasons"]))

ts5 = [task("ready-other", owner_id=BOB, title="Bob's task"), task("done1", status="done")]
rec5 = coord.recommend_for_user(ALICE, ts5, [], set(), {u["id"]: u for u in users}, set(), NOW)
check("never recommends a task actively owned by someone else",
      rec5["recommendation"] is None, str(rec5.get("recommendation")))

ts6, _ = setup_world()
rejected_ids = coord.rejected_task_ids_from_events(
    [{"type": "recommendation_rejected", "payload": {"user_id": ALICE, "task_id": "ready-free"}, "created_at": (NOW - timedelta(days=1)).isoformat()}],
    ALICE, NOW,
)
rec6 = coord.recommend_for_user(ALICE, [t for t in ts6 if t["id"] in ("ready-free", "ready-other")], [], set(),
                                {u["id"]: u for u in users}, rejected_ids, NOW)
check("rejected task is excluded from recommendations",
      rec6["recommendation"] is None, str(rec6.get("recommendation")))

stale_rej = coord.rejected_task_ids_from_events(
    [{"type": "recommendation_rejected", "payload": {"user_id": ALICE, "task_id": "ready-free"}, "created_at": (NOW - timedelta(days=8)).isoformat()}],
    ALICE, NOW,
)
check("rejections expire after 7 days", stale_rej == set())
other_user_rej = coord.rejected_task_ids_from_events(
    [{"type": "recommendation_rejected", "payload": {"user_id": BOB, "task_id": "ready-free"}, "created_at": NOW.isoformat()}],
    ALICE, NOW,
)
check("rejections are per-user", other_user_rej == set())

empty = coord.recommend_for_user(ALICE, [], [], set(), {u["id"]: u for u in users}, set(), NOW)
check("empty project -> honest note, no invented work", empty["recommendation"] is None and empty.get("note"))

# ---------------------------------------------------------------------------
# 5. Pure units — overlap + contract collisions
# ---------------------------------------------------------------------------
print("\n== pure: overlap / duplicate detection ==")
ov = coord.detect_task_overlaps([
    task("a", title="Implement login API", description="email + password authentication endpoint"),
    task("b", title="Build authentication backend for login", description="password auth session handling"),
    task("c", title="Dark mode toggle"),
])
ids = {(p["task_a"]["id"], p["task_b"]["id"]) for p in ov}
check("overlap detected between similar tasks", ("a", "b") in ids, str(ov))
check("no overlap flagged for unrelated tasks", all("c" not in pair for pair in ids))
check("overlap carries shared terms + similarity", all("shared_terms" in p and "similarity" in p for p in ov))
check("done tasks never overlap", coord.detect_task_overlaps([
    task("x", status="done", title="Implement login API"), task("y", title="Implement login API")]) == [])

col = coord.detect_contract_collisions(
    [task("t1"), task("t2")],
    [
        {"id": "c1", "route": "/api/auth/login", "method": "POST", "created_by_task_id": "t1"},
        {"id": "c2", "route": "/api/auth/login", "method": "POST", "created_by_task_id": "t2"},
        {"id": "c3", "route": "/api/other", "method": "GET", "created_by_task_id": "t1"},
    ],
)
check("two open tasks on one contract route flagged", len(col) == 1 and len(col[0]["tasks"]) == 2, str(col))
check("collision route/method grounded", col[0]["route"] == "/api/auth/login" and col[0]["method"] == "POST")
check("single-task contract is not a collision", coord.detect_contract_collisions(
    [task("t1")], [{"id": "c1", "route": "/a", "method": "GET", "created_by_task_id": "t1"}]) == [])
check("done-task contracts ignored", coord.detect_contract_collisions(
    [task("t1", status="done"), task("t2", status="done")],
    [{"id": "c1", "route": "/a", "method": "GET", "created_by_task_id": "t1"},
     {"id": "c2", "route": "/a", "method": "GET", "created_by_task_id": "t2"}]) == [])

# ---------------------------------------------------------------------------
# 6. Pure units — project next action
# ---------------------------------------------------------------------------
print("\n== pure: project next action ==")
def world_for_action():
    ts = [
        task("dep-open", status="in_progress", owner_id=BOB, title="open dep"),
        task("waiting", owner_id=ALICE, title="waiting child"),
        task("ready-free", title="free ready"),
    ]
    return ts, [("waiting", "dep-open")]

tsA, esA = world_for_action()
incA = coord.incomplete_deps_per_task(tsA, esA)
act = coord.project_next_action(tsA, esA, set(), [], NOW)
check("no conflicts/review -> unblock by finishing the dependency",
      act["kind"] == "unblock_task" and act["task"]["id"] == "dep-open", str(act))
check("unblock action explains what it unlocks", any("waiting child" in r for r in act["reasons"]), str(act["reasons"]))

act2 = coord.project_next_action(tsA, esA, set(), [{"id": "x", "description": "CONFLICT conflicting shape: POST /api/auth/login"}], NOW)
check("conflict outranks everything", act2["kind"] == "resolve_conflict", str(act2["kind"]))
check("conflict action carries the grounded conflict", act2["conflict"]["id"] == "x")

tsR = [t | {"status": "review"} if t["id"] == "dep-open" else t for t in tsA]
act3 = coord.project_next_action(tsR, esA, set(), [], NOW)
check("review outranks unblock/start", act3["kind"] == "review_task", str(act3["kind"]))

tsS = [t | {"status": "in_progress", "owner_id": BOB} if t["id"] == "dep-open" else t for t in tsA]
tsS = [t for t in tsS if t["id"] != "waiting"]
act4 = coord.project_next_action(tsS, [], set(), [], NOW)
check("falls through to start_task on ready work", act4["kind"] == "start_task" and act4["task"]["id"] == "ready-free", str(act4))
check("start action notes unassigned claimability", any("unassigned" in r for r in act4["reasons"]), str(act4["reasons"]))

act5 = coord.project_next_action([task("d", status="done")], [], set(), [], NOW)
check("all clear is honest when nothing is actionable", act5["kind"] == "all_clear")

counts = coord.project_state_counts(tsA, esA, set())
check("state counts cover all six states", set(counts) == set(coord.TASK_STATES), str(counts))
check("state counts classified correctly", counts[coord.IN_PROGRESS] == 1 and counts[coord.WAITING] == 1 and counts[coord.READY] == 1, str(counts))

w = coord.who_is_doing_what(tsA, users + [{"id": AGENT, "name": "Scout", "kind": "agent"}])
check("who_is_doing_what lists only owners with open work", len(w) == 2, str(w))
check("who_is_doing_what includes agent kind", all(r["kind"] in ("developer", "agent") for r in w))

# ---------------------------------------------------------------------------
# 7. DB-backed route legs — skipped loudly without DATABASE_URL
# ---------------------------------------------------------------------------
print("\n== routes against the real DB (skipped when no engine/.env) ==")
from app.config import settings  # noqa: E402

db_url = (settings.database_url or "").strip()
if not db_url:
    print("  SKIP  DATABASE_URL not set on this machine — run on a machine with engine/.env")
else:
    import sqlalchemy as sa  # noqa: E402
    from starlette.testclient import TestClient  # noqa: E402

    from app.db.models import Event  # noqa: E402
    from app.db.session import _init  # noqa: E402
    from app.main import app  # noqa: E402

    client = TestClient(app, raise_server_exceptions=False)
    # Phase 6: identity comes from the Bearer token; POST /projects makes the
    # CALLER the owner, so one owner token covers every call below.
    client.headers.update(auth.auth_headers(auth.jwt_for(auth.OWNER_SUB)))
    SessionLocal = _init()
    db = SessionLocal()

    pid = None
    try:
        r = client.post("/projects", json={"name": f"phase4-test-{uuid.uuid4().hex[:8]}"})
        check("project created for the phase4 route pass", r.status_code == 201, r.text)
        pid = r.json()["id"]

        # members: one developer joins via invite; an agent registers (Phase 3 routes)
        code = client.post(f"/projects/{pid}/invite").json()["code"]
        alice_jwt, _alice_sub = auth.extra_member("Alice (P4)")
        alice = client.post(
            "/projects/join",
            json={"code": code, "name": "Alice (P4)", "role": "backend"},
            headers=auth.auth_headers(alice_jwt),
        ).json()["user_id"]
        agent = client.post(f"/projects/{pid}/agents", json={"name": "Scout (P4)", "provider": "opencode", "model": "any"}).json()["id"]

        r = client.get(f"/projects/{pid}/recommendations", params={"user_id": alice})
        check("GET /recommendations 200 with member scoping", r.status_code == 200, r.text)
        check("unknown member rejected 400",
              client.get(f"/projects/{pid}/recommendations", params={"user_id": str(uuid.uuid4())}).status_code == 400)
        check("membership check does not leak other-project users",
              client.get("/projects/join") and client.get(
                  f"/projects/{pid}/recommendations", params={"user_id": ALICE}).status_code == 400)

        # -- build the demo world: done / in_progress / review / blocked / waiting / ready
        t_done = client.post(f"/projects/{pid}/tasks", json={"title": "Design auth schema"}).json()["id"]
        client.patch(f"/projects/{pid}/tasks/{t_done}", json={"status": "done"})

        t_prog = client.post(f"/projects/{pid}/tasks", json={"title": "Implement login endpoint", "owner_id": alice, "priority": "high"}).json()["id"]
        client.patch(f"/projects/{pid}/tasks/{t_prog}", json={"status": "in_progress"})

        t_review = client.post(f"/projects/{pid}/tasks", json={"title": "Review signup flow"}).json()["id"]
        client.patch(f"/projects/{pid}/tasks/{t_review}", json={"status": "review"})

        t_blocked = client.post(f"/projects/{pid}/tasks", json={"title": "Password reset logic", "owner_id": alice}).json()["id"]
        client.patch(f"/projects/{pid}/tasks/{t_blocked}", json={"blocked": True, "blocker_reason": "waiting on design sign-off"})

        t_waiting = client.post(f"/projects/{pid}/tasks", json={"title": "Build dashboard auth", "dependencies": [t_prog]}).json()["id"]
        check("waiting task classified by dependency", client.get(f"/projects/{pid}/tasks").json()[0] is not None)

        t_ready = client.post(f"/projects/{pid}/tasks", json={"title": "Add rate limiting", "priority": "urgent"}).json()["id"]

        # -- ready endpoint ---------------------------------------------------
        r = client.get(f"/projects/{pid}/tasks/ready")
        body = r.json()
        check("GET /tasks/ready 200", r.status_code == 200, r.text)
        ready_ids = [t["id"] for t in body["ready_tasks"]]
        check("done task not ready", t_done not in ready_ids)
        check("in_progress task not ready", t_prog not in ready_ids)
        check("review task not ready", t_review not in ready_ids)
        check("manually blocked task not ready", t_blocked not in ready_ids)
        check("task waiting on incomplete dependency not ready", t_waiting not in ready_ids)
        check("unblocked todo task IS ready", t_ready in ready_ids, str(ready_ids))
        check("ready rows carry explainable reasons", all(t["reasons"] for t in body["ready_tasks"]))
        check("ready rows carry score_factors (no opaque score)", all(t["score_factors"] for t in body["ready_tasks"]))
        check("state counts include every Phase 4 state", set(body["project_task_states"]) == set(coord.TASK_STATES), str(body["project_task_states"]))

        # completing the dependency makes the waiting task ready (dependency-aware)
        client.patch(f"/projects/{pid}/tasks/{t_prog}", json={"status": "done"})
        ready_ids2 = [t["id"] for t in client.get(f"/projects/{pid}/tasks/ready").json()["ready_tasks"]]
        check("dependency completed -> waiting task becomes ready", t_waiting in ready_ids2, str(ready_ids2))
        client.patch(f"/projects/{pid}/tasks/{t_prog}", json={"status": "in_progress"})

        # -- recommendations --------------------------------------------------
        r = client.get(f"/projects/{pid}/recommendations", params={"user_id": alice})
        rec = r.json()
        check("GET /recommendations 200", r.status_code == 200, r.text)
        check("recommendation names the user", rec["user"]["id"] == alice)
        check("current_work shows the assigned in-progress task",
              any(w["id"] == t_prog for w in rec["current_work"]), str(rec["current_work"]))
        check("blocked_work reports the manual blocker with reason",
              any(b["id"] == t_blocked and any("manual blocker" in r for r in b["waiting_on"]) for b in rec["blocked_work"]), str(rec["blocked_work"]))
        check("conflict_awareness section present", "conflict_awareness" in rec)

        # agent-aware recommendations: the agent's own pipeline over its identity
        r = client.get(f"/projects/{pid}/recommendations", params={"user_id": agent})
        rec_agent = r.json()
        check("agent gets its own recommendation (no cross-user leak)",
              r.status_code == 200 and rec_agent["user"]["kind"] == "agent", r.text)
        check("agent with no own work pointed at claimable/unblocked work or honest note",
              rec_agent["recommendation"] is None or isinstance(rec_agent["recommendation"], dict))
        check("agent recommendation differs from alice's when ownership differs",
              (rec_agent.get("recommendation") or {}).get("task", {}).get("id")
              != (rec.get("recommendation") or {}).get("task", {}).get("id")
              or rec_agent.get("recommendation") == rec.get("recommendation"))

        r = client.get(f"/projects/{pid}/recommendations/next", params={"user_id": alice, "explain": "false"})
        check("GET /recommendations/next returns single recommendation",
              r.status_code == 200 and ("recommendation" in r.json()), r.text)
        check("no LLM call without explain=true (fast path)",
              "explanation" not in (r.json().get("recommendation") or {}))

        # -- accept (human override: claims the task) --------------------------
        r = client.post(f"/projects/{pid}/tasks/{t_ready}/accept-recommendation", json={"user_id": agent})
        acc = r.json()
        check("POST accept-recommendation 201", r.status_code == 201, r.text)
        check("accept assigns the task to the acceptor", acc["task"]["owner_id"] == agent, str(acc))
        r = client.post(f"/projects/{pid}/tasks/{t_ready}/accept-recommendation", json={"user_id": agent})
        check("re-accept is idempotent 200", r.status_code == 200 and r.json()["idempotent"] is True, r.text)

        # -- reject (human override: stop suggesting) --------------------------
        client.patch(f"/projects/{pid}/tasks/{t_ready}", json={"owner_id": None})
        r = client.post(f"/projects/{pid}/tasks/{t_ready}/reject-recommendation", json={"user_id": agent, "note": "will take the auth task instead"})
        check("POST reject-recommendation 200", r.status_code == 200, r.text)

        # -- authorization / scoping ------------------------------------------
        check("accept requires a project member",
              client.post(f"/projects/{pid}/tasks/{t_ready}/accept-recommendation", json={"user_id": str(uuid.uuid4())}).status_code == 400)
        check("accept 404s on unknown task",
              client.post(f"/projects/{pid}/tasks/{uuid.uuid4()}/accept-recommendation", json={"user_id": agent}).status_code == 404)
        check("ready endpoint 404s on unknown project",
              client.get(f"/projects/{uuid.uuid4()}/tasks/ready").status_code == 404)
        check("coordination endpoint 404s on unknown project",
              client.get(f"/projects/{uuid.uuid4()}/coordination").status_code == 404)

        # -- project-level next action ------------------------------------------
        r = client.post(f"/projects/{pid}/recommendations/next", json={"user_id": alice})
        act = r.json()
        check("POST /recommendations/next 200 with action", r.status_code == 200 and "action" in act, r.text)
        check("action kind is one of the five deterministic kinds",
              act["action"]["kind"] in ("resolve_conflict", "review_task", "unblock_task", "start_task", "all_clear"), str(act["action"]["kind"]))
        check("action reasons always present", bool(act["action"]["reasons"]))

        # -- coordination summary ------------------------------------------------
        r = client.get(f"/projects/{pid}/coordination")
        cs = r.json()
        check("GET /coordination 200", r.status_code == 200, r.text)
        for section in ("ready_to_start", "blocked", "needs_review", "conflicts", "recommended_next_step", "who_is_doing_what", "task_states"):
            check(f"coordination section present: {section}", section in cs, str(list(cs)))
        check("coordination ready section has the ready task", any(t["id"] == t_ready for t in cs["ready_to_start"]), str(cs["ready_to_start"]))
        check("coordination blocked section has the blocked task", any(b["id"] == t_blocked for b in cs["blocked"]), str(cs["blocked"]))
        check("coordination review section has the review task", any(t["id"] == t_review for t in cs["needs_review"]), str(cs["needs_review"]))
        check("who_is_doing_what grounded in owner_id", any(w["user_id"] == alice for w in cs["who_is_doing_what"]), str(cs["who_is_doing_what"]))

        # -- conflict awareness through real Scaffold state ----------------------
        r = client.post(f"/projects/{pid}/contracts", json={
            "route": "/api/payments/checkout", "method": "POST",
            "request_schema": {"amount": "int"}, "response_schema": {"url": "str"}, "created_by_task_id": t_ready,
        })
        check("contract registered for collision test", r.status_code == 201, r.text)
        r = client.post(f"/projects/{pid}/contracts", json={
            "route": "/api/payments/checkout", "method": "POST",
            "request_schema": {"amount_cents": "int"}, "response_schema": {"session": "str"}, "created_by_task_id": t_prog,
        })
        check("second conflicting contract registered", r.status_code == 201, r.text)
        cs = client.get(f"/projects/{pid}/coordination").json()
        check("contract collision surfaced (two open tasks, same route)", len(cs["conflicts"]["contract_collisions"]) == 1, str(cs["conflicts"]))
        check("open contract conflict counted from blockers table", len(cs["conflicts"]["open_contract_conflicts"]) >= 1, str(cs["conflicts"]["open_contract_conflicts"]))
        act = client.post(f"/projects/{pid}/recommendations/next", json={}).json()
        check("project next action becomes resolve_conflict after a real conflict", act["action"]["kind"] == "resolve_conflict", str(act["action"]))

        # -- events: new Phase 4 event types actually written ---------------------
        rows = db.scalars(sa.select(Event).where(Event.project_id == uuid.UUID(pid))).all()
        types_seen = {e.type for e in rows}
        check("recommendation_accepted event written", "recommendation_accepted" in types_seen, str(sorted(types_seen)))
        check("recommendation_rejected event written", "recommendation_rejected" in types_seen)

        # -- frozen contract spot-checks (Phase 1-3 untouched) --------------------
        r = client.get(f"/projects/{pid}/tasks")
        t0 = next(t for t in r.json() if t["id"] == t_ready)
        check("frozen TaskOut fields unchanged", {"id", "title", "status", "owner_id", "due_at", "created_at"} <= set(t0))
        ctx = client.get(f"/projects/{pid}/context").json()
        check("frozen context shape unchanged", {"project", "tasks", "active_tasks", "generated_at"} <= set(ctx))
        check("frozen task_created payload shape unchanged",
              all(set(e.payload.keys()) == {"task_id", "title"} for e in rows if e.type == "task_created"))

    finally:
        try:
            if pid:
                for stmt in (
                    "DELETE FROM blockers WHERE project_id = :p",
                    "DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)",
                    "DELETE FROM decision_affects_tasks WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)",
                    "DELETE FROM api_contracts WHERE project_id = :p",
                    "DELETE FROM events WHERE project_id = :p",
                    "DELETE FROM tasks WHERE project_id = :p",
                    "DELETE FROM users WHERE project_id = :p",
                    "DELETE FROM projects WHERE id = :p",
                ):
                    db.execute(sa.text(stmt), {"p": pid})
                db.commit()
        except Exception:  # noqa: BLE001 — cleanup must never mask test results
            db.rollback()
        db.close()

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)

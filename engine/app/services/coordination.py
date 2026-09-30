"""coordination.py — Phase 4 (intelligent coordination): deterministic project intelligence.

Repo rule #3: "what's ready / what's blocked / what should happen next" is plain
Python over data already in Postgres — never an LLM call. This module mirrors
the shape of services/team.py (Phase 3) and services/availability.py (Day 5):
every decision function is pure (rows in, rows out) and unit-testable without a
live database. The route layer (`app/routes/coordination.py`) and the MCP server
do the DB I/O and call these functions.

Task states (Phase 4 vocabulary over the existing relational graph):
    READY                  todo + not blocked + all dependencies done
    BLOCKED                an open blocker row / manual blocked switch is active
    WAITING_ON_DEPENDENCY  todo but at least one dependency is not done
    IN_PROGRESS            actively being worked
    REVIEW                 waiting for review
    DONE                   complete

The LLM's only Phase 4 role is *phrasing* an explanation of facts computed here
(reasoning.explain_recommendation); it never decides what is ready or what ranks
first, and the API works fully without it (fail-open).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Vocabulary + tunables (all deterministic; no ML, no opaque scoring)
# ---------------------------------------------------------------------------

READY = "READY"
BLOCKED = "BLOCKED"
WAITING = "WAITING_ON_DEPENDENCY"
IN_PROGRESS = "IN_PROGRESS"
REVIEW = "REVIEW"
DONE = "DONE"

TASK_STATES = (READY, BLOCKED, WAITING, IN_PROGRESS, REVIEW, DONE)

# Explainable score factors — every point is accounted for in `score_factors`
# and echoed back to the client as human-readable reasons. No hidden weights.
PRIORITY_SCORES = {"urgent": 40, "high": 30, "medium": 20, "low": 10}
ASSIGNMENT_BONUS = 25          # task is assigned to the requester
DOWNSTREAM_BONUS = 4           # per open downstream task (capped)
DOWNSTREAM_CAP = 20
DUE_SOON_BONUS = 10            # due within 48h
DUE_WEEK_BONUS = 5             # due within 7 days
AGE_BONUS = 2                  # open for more than a week (gentle aging)
DUE_SOON = timedelta(hours=48)
DUE_WEEK = timedelta(days=7)
AGE_THRESHOLD = timedelta(days=7)

REJECTION_WINDOW = timedelta(days=7)  # how long a rejected recommendation stays excluded

MAX_OVERLAP_PAIRS = 5
MIN_SHARED_TERMS = 2           # a candidate overlap must share at least this many terms
MIN_JACCARD = 0.2             # ...and reach this token-Jaccard similarity

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "is",
    "are", "be", "at", "by", "from", "as", "it", "its", "this", "that", "we",
    "our", "us", "new", "add", "use", "using", "make", "set", "get", "into",
    "up", "out", "all", "any", "can", "will", "should", "when", "then",
}


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize to aware UTC (mirrors availability.py's helper)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Dependency-graph helpers (pure, over plain edge tuples)
# ---------------------------------------------------------------------------

def incomplete_deps_per_task(tasks: list[dict], dep_edges: list[tuple[str, str]]) -> dict[str, set[str]]:
    """task_id -> set of its dependency ids that are not done yet.

    tasks: [{id, status, ...}]   dep_edges: [(task_id, depends_on_task_id)]
    """
    status_by_id = {t["id"]: t.get("status") for t in tasks}
    out: dict[str, set[str]] = {}
    for task_id, depends_on in dep_edges:
        if status_by_id.get(depends_on) != "done":
            out.setdefault(task_id, set()).add(depends_on)
    return out


def downstream_open_counts(tasks: list[dict], dep_edges: list[tuple[str, str]]) -> dict[str, int]:
    """task_id -> how many OPEN (non-done) tasks depend on it — the dependency
    impact factor ("N tasks are waiting on this")."""
    open_ids = {t["id"] for t in tasks if t.get("status") != "done"}
    counts: dict[str, int] = {}
    for task_id, depends_on in dep_edges:
        if task_id in open_ids:
            counts[depends_on] = counts.get(depends_on, 0) + 1
    return counts


def cross_owner_dependencies(
    tasks: list[dict], dep_edges: list[tuple[str, str]], users_by_id: dict[str, dict]
) -> list[dict]:
    """Open dependency edges whose two ends have different owners — the real,
    grounded form of "Agent B started work that waits on Agent A". Never
    inferred from filenames; straight from task_dependencies + tasks.owner_id."""
    by_id = {t["id"]: t for t in tasks}

    def _owner_name(task: dict) -> str | None:
        owner = task.get("owner_id")
        if not owner:
            return None
        u = users_by_id.get(owner)
        return (u or {}).get("name") or owner

    rows: list[dict] = []
    for task_id, depends_on in dep_edges:
        t, d = by_id.get(task_id), by_id.get(depends_on)
        if not t or not d:
            continue
        if t.get("status") == "done" or d.get("status") == "done":
            continue
        if t.get("owner_id") and d.get("owner_id") and t["owner_id"] != d["owner_id"]:
            rows.append(
                {
                    "waiting_task": {"id": t["id"], "title": t["title"], "owner_id": t["owner_id"]},
                    "waiting_owner": _owner_name(t),
                    "blocking_task": {"id": d["id"], "title": d["title"], "owner_id": d["owner_id"]},
                    "blocking_owner": _owner_name(d),
                }
            )
    rows.sort(key=lambda r: (r["waiting_task"]["title"], r["blocking_task"]["title"]))
    return rows


# ---------------------------------------------------------------------------
# State classification (Feature 1 + Feature 4)
# ---------------------------------------------------------------------------

def classify_task(
    task: dict,
    open_blocker_task_ids: set[str],
    incomplete_dep_ids: set[str],
) -> str:
    """One of TASK_STATES for a single task row.

    Precedence: DONE > REVIEW > BLOCKED (explicit human/contract blocker) >
    WAITING_ON_DEPENDENCY (todo only) > IN_PROGRESS > READY.
    An in_progress task with incomplete deps stays IN_PROGRESS — work has begun;
    an in_progress task with an open blocker reads BLOCKED (blockers win for
    visibility — that's what a teammate most needs to see).
    """
    status = task.get("status")
    task_id = task["id"]
    if status == "done":
        return DONE
    if status == "review":
        return REVIEW
    if task.get("blocked") or task_id in open_blocker_task_ids:
        return BLOCKED
    if status == "todo" and incomplete_dep_ids:
        return WAITING
    if status == "in_progress":
        return IN_PROGRESS
    if status == "todo":
        return READY
    return IN_PROGRESS  # unknown status — the DB CHECK makes this unreachable


def is_ready(task: dict, open_blocker_task_ids: set[str], incomplete_dep_ids: set[str]) -> bool:
    """READY = todo, not blocked, all dependencies done. Assigned OR unassigned
    both count (an unassigned ready task is claimable work)."""
    return classify_task(task, open_blocker_task_ids, incomplete_dep_ids) == READY


def ready_reasons(task: dict, incomplete_dep_ids: set[str], downstream_open: int) -> list[str]:
    """Human-readable, database-grounded reasons a task is ready."""
    reasons = ["status is todo"]
    reasons.append("no blocker is active")
    dep_total = task.get("dependency_count", 0)  # set by the route from the dep edges
    if dep_total:
        reasons.append(f"all {dep_total} dependenc{'ies' if dep_total != 1 else 'y'} complete")
    else:
        reasons.append("no dependencies")
    if downstream_open:
        reasons.append(f"{downstream_open} open task(s) depend on it")
    return reasons


# ---------------------------------------------------------------------------
# Explainable ranking (Feature 2 + Feature 3)
# ---------------------------------------------------------------------------

def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def score_candidate(
    task: dict,
    user_id: str | None,
    downstream_open: int,
    now: datetime,
) -> tuple[int, dict, list[str]]:
    """Explainable score for one candidate. Returns (score, factors, reasons).

    Every factor is explicit and echoed to the client — no opaque ML score:
      priority   urgent 40 / high 30 / medium 20 / low 10
      assignment +25 when the task is assigned to the requester
      downstream +4 per open dependent task (cap 20)
      deadline   +10 due within 48h, +5 within 7 days
      age        +2 open for more than a week
    """
    now = _as_utc(now)
    factors: dict[str, int] = {}
    reasons: list[str] = []

    priority = task.get("priority") or "medium"
    factors["priority"] = PRIORITY_SCORES.get(priority, PRIORITY_SCORES["medium"])
    reasons.append(f"{priority} priority")

    if user_id and task.get("owner_id") and task["owner_id"] == user_id:
        factors["assignment"] = ASSIGNMENT_BONUS
        reasons.append("assigned to you")

    if downstream_open:
        factors["downstream"] = min(downstream_open * DOWNSTREAM_BONUS, DOWNSTREAM_CAP)
        reasons.append(f"{downstream_open} downstream task(s) depend on it")

    due = _parse_dt(task.get("due_at"))
    if due and now:
        delta = due - now
        if timedelta(0) <= delta <= DUE_SOON:
            factors["deadline"] = DUE_SOON_BONUS
            reasons.append("due within 48 hours")
        elif timedelta(0) <= delta <= DUE_WEEK:
            factors["deadline"] = DUE_WEEK_BONUS
            reasons.append("due within a week")

    created = _parse_dt(task.get("created_at"))
    if created and now and (now - created) > AGE_THRESHOLD:
        factors["age"] = AGE_BONUS
        reasons.append("open for over a week")

    return sum(factors.values()), factors, reasons


def _sort_time(task: dict) -> datetime:
    return _parse_dt(task.get("created_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc)


def rank_candidates(
    tasks: list[dict],
    user_id: str | None,
    downstream_open: dict[str, int],
    now: datetime,
    incomplete_deps: dict[str, set[str]],
    open_blocker_task_ids: set[str],
) -> list[dict]:
    """Rank ready tasks for a requester: score desc, then oldest first, then id
    (total deterministic order). Each row carries `reasons` + `score_factors`.
    Only READY tasks are ranked here — the route filters first."""
    ranked: list[dict] = []
    for t in tasks:
        d = downstream_open.get(t["id"], 0)
        score, factors, reasons = score_candidate(t, user_id, d, now)
        base = ready_reasons(t, incomplete_deps.get(t["id"], set()), d)
        # de-dupe while keeping order (base may repeat the downstream reason)
        seen: set[str] = set()
        merged = [r for r in base + reasons if not (r in seen or seen.add(r))]
        ranked.append(
            {
                **t,
                "score": score,
                "score_factors": factors,
                "reasons": merged,
                "downstream_open": d,
            }
        )
    ranked.sort(key=lambda r: (-r["score"], _sort_time(r), r["id"]))
    return ranked


# ---------------------------------------------------------------------------
# Personal recommendation (Feature 2 + Feature 9): "What should I work on?"
# ---------------------------------------------------------------------------

def recommend_for_user(
    user_id: str,
    tasks: list[dict],
    dep_edges: list[tuple[str, str]],
    open_blocker_task_ids: set[str],
    users_by_id: dict[str, dict],
    rejected_task_ids: set[str],
    now: datetime,
) -> dict:
    """Deterministic "what should I work on?" for one member (developer OR agent
    — both are `users` rows; nothing here cares which provider an agent uses).

    Pipeline (all deterministic, in order):
      1. drop done tasks
      2. drop tasks this user rejected recently (recommendation_rejected events)
      3. never recommend tasks actively owned by someone else
      4. prefer the requester's own ready (assigned, unblocked, deps-done) work
      5. else prefer continuing their in_progress work
      6. else their blocked work suggests which ready dependency would unblock it
      7. else the top unassigned ready task (claimable)
      8. rank within each pool with the explainable scorer
    """
    incomplete = incomplete_deps_per_task(tasks, dep_edges)
    downstream = downstream_open_counts(tasks, dep_edges)
    by_id = {t["id"]: t for t in tasks}

    def _summary(t: dict) -> dict:
        return {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t.get("priority") or "medium",
            "owner_id": t.get("owner_id"),
            "due_at": t.get("due_at"),
            "created_at": t.get("created_at"),
        }

    def _blocked_by_titles(t: dict) -> list[str]:
        titles = [by_id[d]["title"] for d in sorted(incomplete.get(t["id"], set())) if d in by_id]
        if t.get("blocked") or t["id"] in open_blocker_task_ids:
            titles = titles + ["(manual blocker active)"]
        return titles

    mine_open = [
        t for t in tasks
        if t.get("owner_id") == user_id and t.get("status") != "done" and t["id"] not in rejected_task_ids
    ]
    unassigned_ready = [
        t for t in tasks
        if not t.get("owner_id")
        and t.get("status") == "todo"
        and t["id"] not in rejected_task_ids
        and is_ready(t, open_blocker_task_ids, incomplete.get(t["id"], set()))
    ]

    result: dict = {
        "recommendation": None,
        "alternates": [],
        "current_work": [_summary(t) | {"state": classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set()))}
                         for t in mine_open if t.get("status") == "in_progress"],
        "blocked_work": [
            _summary(t)
            | {"state": classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set())),
               "waiting_on": _blocked_by_titles(t)}
            for t in mine_open
            if classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set())) in (BLOCKED, WAITING)
        ],
        "claimable_count": len(unassigned_ready),
    }

    def _wrap(task: dict, state: str, extra_reasons: list[str]) -> dict:
        d = downstream.get(task["id"], 0)
        score, factors, reasons = score_candidate(task, user_id, d, now)
        seen: set[str] = set()
        all_reasons = [r for r in extra_reasons + reasons if not (r in seen or seen.add(r))]
        return {
            "task": _summary(task),
            "state": state,
            "score": score,
            "score_factors": factors,
            "reasons": all_reasons,
            "downstream_open": d,
        }

    # 1st choice: my own ready work (assigned to me, unblocked, deps done)
    my_ready = [
        t for t in mine_open
        if is_ready(t, open_blocker_task_ids, incomplete.get(t["id"], set()))
    ]
    pool = rank_candidates(my_ready, user_id, downstream, now, incomplete, open_blocker_task_ids)
    if pool:
        top = pool[0]
        result["recommendation"] = _wrap(top, READY, ["assigned to you", "ready to start now"])
        result["alternates"] = [_wrap(t, READY, []) for t in pool[1:4]]
        return result

    # 2nd choice: keep going on what I already started
    in_progress = [t for t in mine_open if t.get("status") == "in_progress"]
    if in_progress:
        top = sorted(in_progress, key=_sort_time)[0]  # oldest first = longest-running
        result["recommendation"] = _wrap(
            top, IN_PROGRESS, ["you already have work in progress — finish it before starting new work"]
        )
        return result

    # 3rd choice: my todo work is blocked/waiting — point at the ready
    # dependency that would unblock it (coordination, not just a to-do list).
    waiting = [t for t in mine_open if classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set())) in (BLOCKED, WAITING)]
    for t in sorted(waiting, key=_sort_time):
        for dep_id in sorted(incomplete.get(t["id"], set())):
            dep = by_id.get(dep_id)
            if not dep or dep.get("status") == "done":
                continue
            dep_state = classify_task(dep, open_blocker_task_ids, incomplete.get(dep_id, set()))
            if dep_state == READY:
                who = "unassigned" if not dep.get("owner_id") else "assigned to someone else"
                result["recommendation"] = _wrap(
                    dep, READY,
                    [f"unblocks your task \"{t['title']}\"", f"that dependency task is {who}"],
                )
                return result
        if t.get("status") == "todo" and classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set())) == BLOCKED:
            continue  # manual blocker — a human must clear it; nothing to recommend here

    # 4th choice: claim unassigned ready work
    pool = rank_candidates(unassigned_ready, user_id, downstream, now, incomplete, open_blocker_task_ids)
    if pool:
        top = pool[0]
        result["recommendation"] = _wrap(top, READY, ["unassigned — free to claim", "ready to start now"])
        result["alternates"] = [_wrap(t, READY, []) for t in pool[1:4]]
        return result

    # Nothing actionable for this user — say so honestly (never invent work).
    result["recommendation"] = None
    result["note"] = (
        "No ready or claimable work found for this member right now."
        if tasks
        else "This project has no tasks yet."
    )
    return result


# ---------------------------------------------------------------------------
# Overlap / duplicate detection (Feature 8) — deterministic metadata first
# ---------------------------------------------------------------------------

def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    words = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def detect_task_overlaps(tasks: list[dict], limit: int = MAX_OVERLAP_PAIRS) -> list[dict]:
    """Likely duplicate work between OPEN tasks, from title+description token
    overlap (Jaccard). Conservative on purpose — this *surfaces* a suspicion
    ("potential overlap detected"); it never mutates anything."""
    open_tasks = [t for t in tasks if t.get("status") != "done"]
    bags = {t["id"]: _tokens(f"{t.get('title') or ''} {t.get('description') or ''}") for t in open_tasks}
    pairs: list[dict] = []
    for i, a in enumerate(open_tasks):
        for b in open_tasks[i + 1:]:
            ta, tb = bags[a["id"]], bags[b["id"]]
            if not ta or not tb:
                continue
            shared = ta & tb
            union = ta | tb
            jacc = len(shared) / len(union) if union else 0.0
            if len(shared) >= MIN_SHARED_TERMS and jacc >= MIN_JACCARD:
                pairs.append(
                    {
                        "task_a": _summary_public(a),
                        "task_b": _summary_public(b),
                        "shared_terms": sorted(shared)[:8],
                        "similarity": round(jacc, 2),
                    }
                )
    pairs.sort(key=lambda p: (-p["similarity"], p["task_a"]["id"], p["task_b"]["id"]))
    return pairs[:limit]


def detect_contract_collisions(tasks: list[dict], contracts: list[dict]) -> list[dict]:
    """Two OPEN tasks that both registered the same method+route contract —
    real Scaffold state (api_contracts.created_by_task_id), never guessed from
    filenames. This is the grounded "two agents are building the same endpoint"
    signal."""
    by_id = {t["id"]: t for t in tasks}
    groups: dict[tuple[str, str], set[str]] = {}
    for c in contracts:
        task_id = c.get("created_by_task_id")
        if not task_id or task_id not in by_id:
            continue
        if by_id[task_id].get("status") == "done":
            continue
        key = ((c.get("route") or "").strip().lower().strip("/"), (c.get("method") or "").upper())
        groups.setdefault(key, set()).add(task_id)

    rows: list[dict] = []
    for (route, method), task_ids in sorted(groups.items()):
        if len(task_ids) < 2:
            continue
        rows.append(
            {
                "route": c_route(contracts, route, method),
                "method": method,
                "tasks": [_summary_public(by_id[tid]) for tid in sorted(task_ids) if tid in by_id],
            }
        )
    return rows


def c_route(contracts: list[dict], route_l: str, method: str) -> str:
    """Recover the original spelling of a normalized route key."""
    for c in contracts:
        if (c.get("route") or "").strip().lower().strip("/") == route_l and (c.get("method") or "").upper() == method:
            return c["route"]
    return route_l


def _summary_public(t: dict) -> dict:
    return {
        "id": t["id"],
        "title": t.get("title"),
        "status": t.get("status"),
        "owner_id": t.get("owner_id"),
    }


# ---------------------------------------------------------------------------
# Project-level next action (Feature 6): "What should happen next?"
# ---------------------------------------------------------------------------

def project_next_action(
    tasks: list[dict],
    dep_edges: list[tuple[str, str]],
    open_blocker_task_ids: set[str],
    open_conflicts: list[dict],
    now: datetime,
) -> dict:
    """The single most valuable next move for the PROJECT, with reasons.

    Deterministic precedence (explainable, fixed):
      1. resolve_conflict — contract-shape conflicts block integration downstream
      2. review_task      — finishing the loop beats starting new work
      3. unblock_task     — point at a ready dependency whose completion unblocks waiting work
      4. start_task       — the top-ranked ready task (unassigned preferred)
      5. all_clear        — honest "nothing actionable"
    """
    incomplete = incomplete_deps_per_task(tasks, dep_edges)
    downstream = downstream_open_counts(tasks, dep_edges)
    by_id = {t["id"]: t for t in tasks}

    def _state(t: dict) -> str:
        return classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set()))

    def _task_out(t: dict) -> dict:
        return _summary_public(t) | {
            "priority": t.get("priority") or "medium",
            "state": _state(t),
            "downstream_open": downstream.get(t["id"], 0),
        }

    if open_conflicts:
        c = open_conflicts[0]
        return {
            "kind": "resolve_conflict",
            "title": f"Resolve the open contract conflict: {c.get('description') or 'contract conflict'}",
            "task": None,
            "conflict": c,
            "reasons": [
                f"{len(open_conflicts)} unresolved contract conflict(s)",
                "conflicting API shapes block everything built on top of them",
                "resolving this first prevents rework across dependent tasks",
            ],
        }

    review = sorted([t for t in tasks if t.get("status") == "review"], key=_sort_time)
    if review:
        t = review[0]
        return {
            "kind": "review_task",
            "title": f"Review \"{t['title']}\"",
            "task": _task_out(t),
            "reasons": [
                f"{len(review)} task(s) waiting in review",
                "review is the oldest unfinished stage — closing the loop unblocks the next dependency",
            ],
        }

    waiting = [t for t in tasks if _state(t) in (BLOCKED, WAITING)]
    for t in sorted(waiting, key=_sort_time):
        for dep_id in sorted(incomplete.get(t["id"], set())):
            dep = by_id.get(dep_id)
            if not dep or dep.get("status") == "done":
                continue
            if dep.get("blocked") or dep_id in open_blocker_task_ids:
                continue
            return {
                "kind": "unblock_task",
                "title": f"Finish \"{dep['title']}\" to unblock \"{t['title']}\"",
                "task": _task_out(dep),
                "reasons": [
                    f"\"{t['title']}\" is waiting on this dependency",
                    f"{downstream.get(dep_id, 0) or 1} downstream task(s) are queued behind it",
                    "completing it unblocks work that is otherwise stalled",
                ],
            }

    ready = [
        t for t in tasks
        if is_ready(t, open_blocker_task_ids, incomplete.get(t["id"], set()))
    ]
    if ready:
        unassigned_first = sorted(ready, key=lambda t: (bool(t.get("owner_id")), _sort_time(t)))
        t = unassigned_first[0]
        reasons = ["ready to start now (todo, unblocked, dependencies complete)"]
        if not t.get("owner_id"):
            reasons.append("unassigned — anyone (or any agent) can claim it")
        d = downstream.get(t["id"], 0)
        if d:
            reasons.append(f"{d} open task(s) depend on it")
        reasons.append(f"{t.get('priority') or 'medium'} priority")
        return {
            "kind": "start_task",
            "title": f"Start \"{t['title']}\"",
            "task": _task_out(t),
            "reasons": reasons,
        }

    return {
        "kind": "all_clear",
        "title": "No immediate next action — everything is either done or owned and moving",
        "task": None,
        "reasons": [
            "no unresolved conflicts, no pending review, no blocked tasks, no unclaimed ready work",
        ],
    }


def project_state_counts(
    tasks: list[dict],
    dep_edges: list[tuple[str, str]],
    open_blocker_task_ids: set[str],
) -> dict:
    """Per-state counts across the whole project (the coordination panel's strip)."""
    incomplete = incomplete_deps_per_task(tasks, dep_edges)
    counts = {s: 0 for s in TASK_STATES}
    for t in tasks:
        counts[classify_task(t, open_blocker_task_ids, incomplete.get(t["id"], set()))] += 1
    return counts


def who_is_doing_what(tasks: list[dict], users: list[dict]) -> list[dict]:
    """Owner -> their open tasks (kind-aware: agents and developers both live in
    `users`). Grounded in tasks.owner_id — never faked presence."""
    by_owner: dict[str, list[dict]] = {}
    for t in tasks:
        if t.get("owner_id") and t.get("status") != "done":
            by_owner.setdefault(t["owner_id"], []).append(t)

    rows: list[dict] = []
    for u in users:
        owned = by_owner.get(u["id"], [])
        if not owned:
            continue
        rows.append(
            {
                "user_id": u["id"],
                "name": u.get("name"),
                "kind": u.get("kind") or "developer",
                "open_tasks": [
                    {"id": t["id"], "title": t["title"], "status": t["status"]} for t in owned
                ],
            }
        )
    rows.sort(key=lambda r: (-len(r["open_tasks"]), (r["name"] or "").lower()))
    return rows


# ---------------------------------------------------------------------------
# Rejection memory (Feature 10): human override, honored deterministically
# ---------------------------------------------------------------------------

def rejected_task_ids_from_events(events: list[dict], user_id: str, now: datetime) -> set[str]:
    """Task ids this user rejected via POST .../reject-recommendation within the
    REJECTION_WINDOW. Grounded in real `recommendation_rejected` events — the
    human's decision is the source of truth, so the recommender stops
    re-suggesting those tasks to them."""
    now = _as_utc(now)
    out: set[str] = set()
    for e in events:
        if e.get("type") != "recommendation_rejected":
            continue
        payload = e.get("payload") or {}
        if payload.get("user_id") != user_id:
            continue
        created = _parse_dt(e.get("created_at"))
        if created and now and (now - created) <= REJECTION_WINDOW:
            task_id = payload.get("task_id")
            if task_id:
                out.add(task_id)
    return out

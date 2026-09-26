# Day 4 Demo — The Conflict Scenario (Agent A vs Agent B)

> The rehearsed "wow moment" (master doc §17, step 4): show an agent *would have*
> diverged, and that Scaffold caught it — deterministically, in seconds, without
> a human in the loop.

## The setup

Two agents, one project (dev/test project `fdcb7511-434b-40c1-a391-0cd45e2150a5`),
feature: **auth**.

1. **Agent A** (Dev A's laptop) prompts: *"Add a login endpoint: POST /api/auth/login
   taking email + password, returning a token."*
   → Contract registered: `POST /api/auth/login`, request `{email, password}`,
   response `{token}`. (Via the plugin "after" hook → `POST /contracts`, or a real
   push → webhook → diff parser.)
2. **Agent B** (Dev B's laptop) prompts the same feature *without* ever being told
   A's design: *"Add a login endpoint that takes username and password."*

## The moment (what the audience sees, ~15 seconds apart)

| Signal | Where | What it shows |
| --- | --- | --- |
| `conflict_flagged` event | Dashboard (Supabase Realtime) | "incoming POST /api/auth/login differs from registered contract — differing fields: email/username, token/jwt" |
| New open blocker | Dashboard / `GET /context` → blockers | Same conflict, phrased as an action item |
| Auto-filed GitHub issue | `themdmohsin/Scaffold` issues, label `scaffold-conflict` | "[SCAFFOLD] Conflicting API contract: POST /api/auth/login" with both shapes side by side |
| Dev B's next prompt | OpenCode session | `get_project_context()` / `get_api_contract("/api/auth/login")` now returns the REAL registered contract — B's agent corrects course before writing more code |

## The honest framing (master doc §5)

- We **prevent** semantic conflicts by serving the real contract before code is written (Part A).
- We **detect** the ones that slip through, deterministically from diffs/registrations, and **surface** them (event + blocker + issue) — this is Part B.
- We do **not** resolve line-level Git merges. Say that plainly if a judge asks.

## What detection actually fired (and why it's trustworthy)

`conflicting_shape` — same method + route, declared schemas differ. Decided by
plain dict comparison in `conflict_service.py` (repo rule #3): no LLM, no
embeddings, deterministic on every replay. The LLM never decides that a conflict
exists; it may only summarize it.

## Fallback paths (if the live network misbehaves)

- No `GITHUB_TOKEN` / GitHub down → event + blocker still land; issue step is skipped (fail-open by design).
- Realtime lag → dashboard refetch picks the event up on next poll.
- Worst case, replay the whole thing through the webhook with the stubbed commit
  from `engine/tests/test_day4b.py` — the pipeline is identical.

## Rehearsal checklist (run 3+ times before the demo)

1. `engine/.env` has `DATABASE_URL`, `GITHUB_TOKEN`, `SCAFFOLD_GITHUB_REPO=themdmohsin/Scaffold`.
2. Engine up: `cd engine && uvicorn app.main:app --reload` → `/health` returns ok.
3. Dashboard up: `cd dashboard && npm run dev` → project view open on both "laptops" (or two windows for the rehearsal).
4. Register Agent A's contract → confirm NO conflict event (clean registration).
5. Push Agent B's divergent contract → confirm event + blocker + GitHub issue appear.
6. Clear the demo state between runs (resolve the blocker, close the issue) so each run looks live.

# HANDOFF
## Current State — 2026-09-28 — OpenCode (PR #6 merged; the last unproven leg PASSED — and it uncovered a real bug; model fallback chain; demo project reset to pristine)

Worked the day6/7 leftover list from `docs/next-session-prompt.md` top to bottom. Branch
`day7-mk-plugin-verification` off `main` (PR #6 merged first, fast-forward, branch deleted).

**1. The one unproven leg — a real OpenCode fork session through the plugin — PASSED, and it
found a real bug.** Scratch worktree (`git init` + `.opencode/plugins/scaffold.ts` copied in),
engine at `localhost:8000`, `bun run <abs path to packages/opencode/src/index.ts> run "..."
--model anthropic/claude-sonnet-5` with the shell's cwd set to the scratch dir (NOT `--dir`:
`run.ts` resolves `InstanceRef` from `process.cwd()` *before* its own `--dir` chdir runs, so
`--dir` only works together with `--attach`; for a local run just cwd into the target directory
before invoking bun — the earlier "hangs silently" report was actually this: `--dir` pointed
`process.chdir` nowhere useful, the instance loaded the wrong project, so nothing looked wrong
but nothing happened either). With cwd fixed, the session ran cleanly: agent wrote
`routes_echo.py` (a `POST /api/echo` FastAPI router), `tool.execute.after` reported it, DB shows
`change_reported {"diff_summary": "wrote routes_echo.py (created, 342 bytes)", ...}`. **Then the
second half of the proof failed**: a fresh second session's injected context block did NOT
mention the change, chars stayed at 715 — because `renderContextBlock` in
`opencode-plugin/.opencode/plugins/scaffold.ts` never read `context.blockers` or
`context.recent_events` at all, despite this file's own 2026-09-26 entry claiming "MCP
`get_project_context()` inherits both keys so agents see the conflict too" (line ~184 below) —
the engine returned the keys, the plugin silently dropped them. **Fixed**: `renderContextBlock`
now renders open `blockers` (capped 5, un-resolved only) and `recent_events` filtered to
`change_reported`/`commit_ingested` (capped 5) as "OPEN BLOCKERS" / "Recent changes from
teammates' sessions" sections, still bounded by the existing 2400-char `truncate()`. Re-ran the
second session: block grew 715 → 1085 chars and now reads (verbatim, via
`python opencode-plugin/tests/acceptance_live.py`):
```
OPEN BLOCKERS — a teammate's change conflicts with something; resolve before continuing:
- [CONFLICT] conflicting shape: incoming POST /api/auth/login vs registered POST /api/auth/login
Recent changes from teammates' sessions:
- wrote routes_echo.py (created, 342 bytes)
- commit 5173f15: routes GET /api/test/scaffold-v2
- commit c7b7de5: routes GET /api/test/scaffold
```
This is the actual headline claim of the product ("live shared awareness ... so agents stop
inventing conflicting designs") and it was not happening before today. Pinned by 4 new
`verify_plugin.ts` checks (open blocker rendered, resolved blocker excluded, teammate's
`change_reported` rendered, non-change events like `teammate_joined` excluded) — 59/59 green;
`acceptance_live.py` and `acceptance_live.py --self-test` both still pass against the live
engine; `mcp_sdk_interop.py` 16/17 (the 1 fail — `plugin driver exited cleanly`, exit
3221226505 — is pre-existing Windows/Node `uv` teardown noise, reproduced identically on `main`
before this change, unrelated to the plugin). `npm run typecheck` has one pre-existing failure in
vendored `packages/sdk/js` (`Headers.entries` under this TS lib target) — also reproduced on
`main`, not touched.

**2. Model fallback chain added to `engine/app/services/reasoning.py`** (the only LLM file,
repo rule #1). `_completion_with_fallback` tries `SCAFFOLD_LLM_MODEL` first (with the existing
503/429/timeout retry), then walks `FALLBACK_MODELS = [gemini/gemini-3.1-flash-lite,
gemini/gemini-3.7-flash, gemini/gemini-3.8-flash]` on a 404/"model unavailable"/exhausted-retry
error; the first model that answers is cached process-wide (`_model_cache["winner"]`) so later
calls skip straight to it instead of re-probing a dead model every time. Both `summarize_diff`
and `answer_prompt` route through it now (previously only `answer_prompt` retried, and neither
fell back across models). Non-transient errors (bad key, etc.) still fail fast, unchanged. Pinned
by 9 new `test_day3.py` checks (classifier units, candidate ordering + cache-to-front, and two
e2e `/reason` calls proving a dead configured model falls back and a second call reuses the
cached winner) — no new env vars, `SCAFFOLD_LLM_MODEL` unchanged as the first-choice override.

**3. Demo project reset to pristine.** Project `fdcb7511-434b-40c1-a391-0cd45e2150a5` had the
prior session's dry-run artifacts (2 extra `POST /api/auth/login` contracts, the `[CONFLICT]`
blocker + its `conflict_flagged`/`contract_registered`×2 events, the "Laptop B (dry-run)" user +
its `teammate_joined` event, the "Implement password-reset logic" task + its `task_created`
event) plus this session's own `routes_echo.py` proof-run `change_reported` event — all deleted
by exact row id (not a blanket wipe). Verified after: 2 tasks (the 2 originals), 1 decision, 3
contracts (`/api/test/scaffold`, `/api/test/scaffold-v2`, `/api/payments/checkout`), 0 blockers,
8 events (the original seed events only). Re-verified untouched after running all four suites
(they use their own `dayX-test-*` / fixture projects, not the demo default).

**Suites: 172/172** (day2 31, day3 42 [+7 from the fallback chain], day4b 25, day5 74) against
live Supabase — all green, engine untouched otherwise.

**Still open / not attempted this session:**
- GitHub PAT still lacks Issues:read-write on themdmohsin/Scaffold — issue #5 stays open (user-side, per repo owner's GitHub settings — not something an agent session can fix).
- The two-real-laptops pass (`docs/day5-integration-runbook.md`) needs a second physical/VM machine — only one machine was available this session; the single-host equivalents (this session's plugin proof + the 2026-09-26 dry-run) are the closest available substitute.
- `day7-mk-plugin-verification` is pushed but not yet PR'd as of this entry — open the PR into `main` before doing anything else.
- The pre-existing `mcp_sdk_interop.py` exit-code flake and the vendored-SDK `npm run typecheck` failure are real but out of scope (not caused by, or fixable via, this repo's own code) — noted so nobody re-investigates them as new regressions.

---
## Current State — 2026-09-28 — Buffy (single-host dry-run PASS; Gemini model swap; real fork session STILL unproven)

Runbook dry-run on the live stack (engine 0.0.0.0:8000 + dashboard): invite → join 201,
re-join idempotent 200 `existing:true`; §17 conflict beat LIVE (A clean, B divergent →
event + blocker + a REAL GitHub issue filed via GITHUB_TOKEN); `/reason` roster assignment
(Gemini picked the just-invited "Laptop B (dry-run)" user by open-task count); dashboard
ask → suggestion chip → click → task landed ASSIGNED (owner verified via API); realtime
pill live; test_day3 35/35.

**Gemini model crisis on the rotated key (`AQ.A…` = "new user")**: gemini-2.5-flash →
404 "no longer available to new users"; gemini-3.6-flash → free pool saturated (503
storms, all 3 retries exhausted); 2.5-pro 404; 3.1-pro-preview 429. Working model found
and set via the frozen override: `SCAFFOLD_LLM_MODEL=gemini/gemini-3.1-flash-lite` in
engine/.env (embeddings unchanged, gemini-embedding-001). Pool still flaps minute-to-
minute — a model fallback chain in reasoning.py is the proper fix. `SCAFFOLD_DEFAULT_PROJECT_ID`
also added (demo project) so plugin `get_project_context()` resolves bare.

**The ONE unproven leg: a real OpenCode fork session driving the plugin.** `opencode run`
(headless, SCAFFOLD_ENGINE_URL set, plugin copied into a scratch worktree) HUNG silently
(300s, no file written, no change_reported event; bun process lingered). Wire format is
already proven (3/3 live MCP acceptance), but the real agent loop never ran. Next session:
retry with the Claude key and debug logging (fork logs under ~/.local/share/opencode/log/).
Demo project has dry-run artifacts (2 extra /api/auth/login contracts + conflict blocker,
"Implement password-reset logic" task, "Laptop B (dry-run)" user) — reset before the real
demo: delete those contracts/tasks/events/blockers rows for project fdcb7511….
PR #6 (day6-mk-retry-and-verification) is OPEN against main — merge it first.

---

## Current State — 2026-09-27 — Buffy (team LLM key live — embeddings backfilled, /reason re-verified)

Session run with SCAFFOLD_TEAM_LLM_KEY set in engine/.env (Google AI Studio, new `AQ.A…` key format —
LiteLLM accepts it unchanged). (1) **Embedding drift closed**: 13 of 57 api_contracts rows (created by
later day2/day5 test+webhook runs) had NULL embeddings; `backfill_embeddings` re-run (idempotent) →
now 57/57 contracts + 21/21 decisions embedded, 0 failed (new rows embed on-write; this was backfill
drift only). (2) **Real /reason re-verified** against the demo project (fdcb7511…): HTTP 200, real
Gemini via LiteLLM (`gemini/gemini-3.6-flash`), verbatim exchange below. (3) `supabase_realtime`
publication re-checked directly in Postgres: tasks/decisions/events all members. (4) Dashboard:
StrictMode removed (it double-subscribed every Realtime channel in dev), `npm run build` green,
test_day3 35/35 re-run. Uncommitted on disk: lib/realtime.ts DELETE-binding fix (banner below) +
src/main.tsx StrictMode removal. Still user-side: GitHub PAT needs Issues: read+write before #5 can
be closed.

Verbatim /reason exchange (2026-09-27, project fdcb7511-434b-40c1-a391-0cd45e2150a5):

```
POST /projects/fdcb7511-434b-40c1-a391-0cd45e2150a5/reason
{"prompt": "We need to add user authentication. Should we roll our own JWT session handling or use a provider? Which existing API contracts does it touch?"}
→ HTTP 200
{"answer": "There is no decision recorded in the project context regarding rolling custom JWT session handling versus using an auth provider. The current project API contracts are GET /api/test/scaffold-v2, GET /api/test/scaffold, and POST /api/payments/checkout (which would require authentication protection).",
 "suggested_tasks": [
   {"title": "Select user authentication approach and update decisions", "owner_id": "7ce9a46f-b657-45d4-ab66-f0474bf31a33", "due_at": null},
   {"title": "Update API contracts for authentication routes", "owner_id": "7ce9a46f-b657-45d4-ab66-f0474bf31a33", "due_at": null}]}
```

---

## Current State — 2026-09-27 — Mohammed + Buffy (blockers cleared: 165/165 suites re-green, realtime verified live)

The three user-side blockers from the pass below are resolved and re-verified end to end.
**(1) Supabase DB password rotated** → engine/.env updated; all four suites re-run against live
Supabase — day2 31, day3 35, day4b 25, day5 74 checks: **165 passed, 0 failed** (first full-suite
green on Supabase since the rotation); the Day 3 migration (pgvector + realtime publication)
applies cleanly. **(2) Realtime VERIFIED — real push, both legs.** dashboard/.env now carries the
VITE_ vars (one correction: `VITE_SUPABASE_URL` must be the bare project URL
`https://<ref>.supabase.co`, NOT `.../rest/v1` — the latter breaks the realtime websocket handshake;
fixed). Joined the demo project, pill shows `live`; a row INSERTed directly into Postgres pushed to
the board with zero interaction, and its DELETE did too after a code fix: the `postgres_changes`
bindings filtered on `project_id`, but DELETE payloads carry only the old row's PK under default
replica identity, so deletes were silently dropped — `dashboard/src/lib/realtime.ts` now adds an
unfiltered DELETE binding per table (the onChange refetch is project-scoped, so cross-project
delete events are harmless). **(3) GitHub PAT STILL 403 on comment/close of issue #5** — the token
is valid (GET /user and repo reads return 200 as themdmohsin) but writes are denied, i.e. the token
in engine/.env has Issues: read-only. Likely a different token than the one whose permissions were
edited, or the permission change wasn't saved. Action: on the exact token pasted in engine/.env,
confirm Repository permissions → Issues → Read and write, then re-verify by commenting/closing #5
(#5 stays open until then). Demo project untouched; verification servers torn down. Uncommitted:
the realtime DELETE-binding fix in dashboard/src/lib/realtime.ts.
---
## Current State — 2026-09-27 — Mohammed + Buffy (post-rehearsal verification pass)
Post-Day-6 closeout per the remaining-open-items list (user-approved deviations from the freeze:
one code change — the 503 retry — plus the real GitHub-issue leg). **Gemini 503 flap FIXED**:
`reasoning.answer_prompt` now retries transient provider errors (503/502/500/504/429/timeout/connection,
2 retries, ~1s/2s backoff) while non-transient errors (bad key etc.) fail fast unchanged — the demo's
only flaky beat self-heals; pinned by new `test_day3` checks (classifier units + "exactly 3 attempts on
flap, 1 on hard error" endpoint tests). **GitHub-issue leg PROVEN LIVE**: the engine's real
`create_conflict_issue` path filed [issue #5](https://github.com/themdmohsin/Scaffold/issues/5)
(conflicting-shape finding, deterministic body). **Blocked on three user-side items:**
(1) **Supabase DB password mismatch** — `DATABASE_URL` in engine/.env gets `password authentication
failed for user "postgres"` (pooler circuit-breaker trips on retry bursts); Day 5/6 verification ran on
the Docker rig, so Supabase was never re-tested after a password rotation. Fix: reset the DB password in
Supabase (Settings → Database) and update `DATABASE_URL`, then re-run all suites (day2/3/4b/5) — they
are written for the real DB and were NOT re-run in this pass (retry change verified by direct
invocation instead). (2) **Fine-grained PAT permissions incomplete** — token creates issues (201) but
is denied comment/close/label (403 "Resource not accessible by personal access token"), so #5 is still
OPEN: regenerate the PAT with Issues: Read and write (or use a classic PAT with repo scope), then close
#5 (or close it manually) — the label `scaffold-conflict` self-creates on the next real conflict once
the token can. (3) **Supabase realtime verification pending** — `dashboard/.env` still has no
`VITE_SUPABASE_URL`/`VITE_SUPABASE_ANON_KEY`; realtime leg (cross-tab push without interaction) runs as
soon as they're added. Demo state untouched; demo project still pristine.
---
## Current State — 2026-09-27 — Prabhanjan (Day 6 rehearsal complete — 3/3 PASS, main is submission-ready)

Day 6 (Rehearsal & Buffer) is done per build plan §2: nothing new was built —
rehearsal, stabilization, and the backup video only. The stack was cold-restarted
from scratch after a machine reboot (Docker Desktop self-start → `scaffold-day5-pg`
`docker start` → engine :8010 via `.venv` python → dashboard :5173 via
`VITE_ENGINE_URL=http://127.0.0.1:8010`; note vite binds IPv6-only, open
`http://localhost:5173/`, not 127.0.0.1).

**§17 conflict scenario rehearsed 3× (PASS every time):** Agent A contract
(POST /api/auth/login {email,password}→{token}) registers clean — no conflict
signal; Agent B divergent shape ({username,password}→{jwt}) deterministically
flags `conflicting_shape` → 1 conflict_flagged event + 1 open blocker every run
(1/1/1 summary; dedupe verified across runs). Verified in DB and via
`GET /context`; dashboard renders the red-flagged blocker + both contract chips
correctly at 1920×1080 (projector pass ✓). **Live /reason leg PASS:**
`live_reason_check` end-to-end with real Gemini (3 suggested tasks,
roster-valid owner_ids). **GitHub-issue leg:** rehearsed fail-open (no local
`GITHUB_TOKEN` → engine skips issue creation before any API call; nothing was
filed against themdmohsin/Scaffold). To light it up for the demo: fine-grained
PAT (Issues: RW on themdmohsin/Scaffold) + `SCAFFOLD_GITHUB_REPO` in
`engine/.env` — optional, demo-able either way.

**Backup video recorded** (build plan Person A requirement, §15 risk
coverage): join → pristine state → A registers → B diverges → Leave/Join
reveal of the [CONFLICT] blocker — saved at
`docs/demo-assets/day6-backup-rehearsal.webm` (untracked; keep out of git,
use for judging fallback). Demo-state reset procedure used between every run:
`delete from blockers/events/api_contracts where project_id='52a9e358-…'`.

**Demo notes (from rehearsal):** dashboard has no polling — updates appear on
action/refresh (refetch-on-action is the rehearsed GO flow; don't refresh
mid-demo, Leave→Join is the reveal); Ask button re-click on Gemini 503 flaps
remains the only known flaky beat. Housekeeping done: `day5-part-A` branch
deleted (local+origin), scratch logs removed, demo project reset to pristine
(0 contracts/events/blockers/tasks). **Verdict: GO. `main` (2917a3a) is the
submission — freeze it; no further commits unless something is actively
broken.**

---

## Current State — 2026-09-26 — Prabhanjan (Day 5 complete — both persons, GO verdict recorded)

Day 5 Person A is built and verified on hardware: deterministic availability
roster (SQL GROUP BY open tasks + hours-until-deadline — never LLM-guessed) fed into `/reason`,
deterministic post-validation of every suggested assignment (unknown owners cleared, past or
unparseable due dates cleared, beyond-deadline dates clamped; response shape frozen, additive
`assignment_notes`), and the invite/join flow (`POST /projects/{id}/invite`,
`POST /projects/join` — stateless signed codes, creates the project's `users` rows, no schema
changes). Six audit rounds across the repo fixed ~27 defects red-then-green; suites:
test_day5 74/74, day2 31/31, day3 29/29, day4b 25/25, plugin 55/55, dashboard build ✓.
Details in the Day 5 entry near the end of this file.

Day 5 Person B's integration pass was executed as a single-host equivalent (no second laptop
available): live engine + plugin MCP acceptance (3/3) + curl second client + a real browser
driving the whole §17 flow — **GO** (residuals below). The pass caught the dashboard's
dead-mutations bug (`pidRef` never assigned — every ask/add/move was a 404) and the
conflict-moment visibility gap. `docs/day5-integration-runbook.md` stays copy-pasteable for a
real two-laptop re-run if hardware ever appears.

Earlier hardware verification (2026-09-26) on a disposable local Postgres+pgvector container
(`scaffold-day5-pg`, port 5433) + a local `engine/.env` let every previously-skipped DB leg run
for real — `test_day5` is now **68/68 including the route legs** (invite → join → idempotent
re-join → roster load), `test_day2` **30/30**, `test_day4b` **25/25** against the same rig, and
`/reason` without an LLM key degrades exactly as designed (503, fail-open). Hardware caught TWO
real bugs the offline layer could never see: the idempotent-join edit had silently dropped
`status_code=201`, so every NEW join returned 200 against the frozen contract (fixed; existing
joins stay 200/`existing`), and with the real `SCAFFOLD_TEAM_LLM_KEY` the live chain
(project → invite → join → roster → Gemini → validated suggestions) first returned zero tasks —
Gemini 3's hidden thinking tokens count against `max_tokens`, so 800 truncated the JSON
mid-string (raised to 2000; pinned by a `test_day5` source check). The live chain then **PASSED**:
suggested tasks carried roster-valid owner_ids end-to-end (`python -m app.scripts.live_reason_check`
reproduces it; transient Gemini 503s are retried). A fifth audit round (both persons, same day)
closed two more live-surface defects before they could ship: a join `role` containing a newline
would have forged its own line inside the LLM TEAM ROSTER block (the name had this fix; the role
did not — roles are now whitespace-collapsed at the join boundary too), and duplicate suggested
task titles survived the parser and collided on the dashboard (suggestion buttons are keyed and
removed by title — the parser now dedupes case-insensitively). `test_day5` pins both by test.
The same round found the demo-moment gap: conflicts were persisted (event + blocker + issue) but
`GET /context` exposed neither and the dashboard had no section — `/context` now returns additive
`blockers` (open, ≤10) and `recent_events` (≤8) keys, the dashboard renders open blockers, and
MCP `get_project_context()` inherits both keys so agents see the conflict too (all pre-existing
context keys byte-identical; pinned by DB-backed tests). The round also hardened the suite
itself: the day5 teardown never deleted `blockers`, so one interrupted run used to poison every
later run through the fixed project name (409 → crash-loop; teardown extended + fixed name kept
for idempotent re-runs). The day2/day3/day4b teardowns leave their own `dayX-test-*` projects
behind — harmless, but purge them off the demo rig after suite runs. Secret hygiene: the real
keys briefly landed in the git-tracked `engine/.env.example` — moved to gitignored `engine/.env`
before any commit.

NO SECOND LAPTOP — single-host equivalent executed instead (2026-09-26): the two-laptop pass
existed to prove multiple independent clients share one engine, so it was run on one machine
with four real clients — (1) live uvicorn engine against the rig, (2) the plugin's real MCP
acceptance runner over the wire (`tests/acceptance_live.py` → 3/3, 267-char block injected,
zero warnings, after seeding the demo project + setting `SCAFFOLD_DEFAULT_PROJECT_ID`),
(3) curl as laptop B (invite → join 201 → idempotent 200/`existing`), and (4) a REAL browser
driving the dashboard end to end: join → live ask → Gemini suggestions → click → task lands
ASSIGNED (owner = the seeded user) → conflict leg → blocker banner visible in the panel.
The browser click caught one more real bug static review missed: `pidRef` was never assigned,
so EVERY dashboard mutation (ask/suggest/add/move) POSTed to `/projects//…` → 404 while reads
still worked — the UI looked alive with every button dead (fixed in `join()`/`leave()`, build ✓).
**Verdict: GO for the single-host demo** — every §17 beat works end to end. Residual risks,
known and accepted: Supabase realtime was off in rehearsal (`VITE_SUPABASE_*` unset; the demo
relies on refetch-on-action, which is what was exercised), all clients share one machine/DB,
Gemini free-tier 503 flaps need a re-click, and `GITHUB_TOKEN` unset locally means the
issue-creation step is skipped (fail-open by design — file it with the token set on demo day).

SIXTH audit round (whole repo, Day 1→5, 2026-09-26): two real defects fixed. (1) The GitHub
webhook ingested pushes from ANY branch — the team's daily feature-branch pushes would have
planted WIP routes as contracts and fired false conflicts; it now ingests only
main/master (backwards compatible with payloads lacking `ref`), pinned by a new test_day2 check
and proven live with a genuinely signed feature-branch push returning
`{processed: [], skipped: {branch}}`. (2) A stray undeclared `GET /api/test/scaffold-v2` route
violated the frozen-contract rule; removed. Re-verified after the fixes: day5 74/74, day2 31/31,
day3 29/29, day4b 25/25, plugin 55/55 + typecheck, dashboard build ✓. HMAC verification,
retrieval fallbacks, conflict detection, schema↔ORM parity and the plugin's fail-open guarantees
all re-read and held.

Day 5 Person A is built and offline-verified (38/38 pure checks): deterministic availability
roster (SQL GROUP BY open tasks + hours-until-deadline — never LLM-guessed) fed into `/reason`,
deterministic post-validation of every suggested assignment (unknown owners cleared, past or
unparseable due dates cleared, beyond-deadline dates clamped; response shape frozen, additive
`assignment_notes`), and the invite/join flow (`POST /projects/{id}/invite`,
`POST /projects/join` — stateless signed codes, creates the project's `users` rows, no schema
changes). Details in the Day 5 entry near the end of this file.

Verified on real hardware (2026-09-26): the DB-backed route sections and the live-LLM
assignment path both ran for real and pass (details above). Person B's Day 5 two-laptop
integration pass is the remaining open Day 5 item.

---

## Current State — 2026-09-25 — Prabhanjan (Day 4 Part A — merged via PR #3)

Day 4 is complete on **both halves**: Part A (this banner) and Part B (Mihika's banner below).

**Day 4 Part A — OpenCode plugin hooks** (`opencode-plugin/.opencode/plugins/scaffold.ts`): the
plugin speaks MCP to the engine at `<SCAFFOLD_ENGINE_URL>/mcp` (hand-rolled JSON-RPC client, no
SDK) and wires the full agent loop — `get_project_context()` injected as a bounded 2400-char
system block via `experimental.chat.system.transform`, `get_api_contract(route)` pinned from
write-tool arguments via `tool.execute.before`, `report_change(diff_summary, files_changed)`
built deterministically from real diff metadata via `tool.execute.after`, context kept across
compaction. Reporting is durable: reports the engine refuses are parked (max 50, kept 1h) and
replayed oldest-first when it recovers.

Verified offline: `node tests/verify_plugin.ts` **55/55** against a wire-faithful fake engine,
`python tests/mcp_sdk_interop.py` **17/17** against the *real* `mcp` SDK server,
`python tests/acceptance_live.py --self-test` **4/4**, and strict `npm run typecheck` against
the vendored plugin types. **Never run live on real hardware:** no real OpenCode session against
the real Postgres-backed engine yet (no `engine/.env` on the dev machine) — that, and the
full two-machine loop, remain the open Day 4 items.

Full details: the "Day 4 — Section A" entry near the end of this file.

---

## Current State — 2026-09-25 — Mihika (Day 4 Part B)

Scaffold is now through **Day 4 Part B: deterministic conflict detection + auto-GitHub-issue** (engine side; Part A plugin hooks are the other half of Day 4).

Conflicts entering via `POST /projects/:id/contracts` (plugin/dashboard) or the GitHub webhook are detected deterministically, logged as `conflict_flagged` events, turned into deduped `blockers` rows, and auto-filed as GitHub issues (label `scaffold-conflict`). See `docs/demo-conflict-scenario.md` for the rehearsed demo.

---

## Current State — 2026-09-24 — Mohammed

Scaffold is currently through **Day 3: reasoning + retrieval + realtime groundwork**.

Day 1, Day 2, and Day 3 implementation work has been completed and pushed to:

`github.com/themdmohsin/Scaffold`

Major verification completed:

- Day 2 automated test suite: **30/30 green**
- Day 3 automated test suite: **29/29 green**
- Dashboard production build: **successful**
- Engine `/health`: **successful**
- Day 3 database migration: **successful**
- Embedding backfill: **successful**
- Real GitHub webhook test: **successful**
- Real `/reason` request: **successful**

The next major milestone is **Day 4: connecting the OpenCode fork to the Scaffold Engine through the plugin hooks**, making the coding agents Scaffold-aware during real coding sessions.

---

# Day 4 Part B — 2026-09-25 — Mihika (solo)

## Conflict Logic + Auto-GitHub-Issue (build plan Day 4, Person B)

Implemented:

- `engine/app/services/conflict_service.py` — pure, deterministic detection (repo rule #3, no LLM):
  - `conflicting_shape` — same method+route, declared request/response schemas differ (both sides must declare schemas; webhook-parsed contracts only prove path+method, so they never false-positive)
  - `method_divergence` — same normalized path (`:id` == `{id}`), different HTTP method
  - `sibling_collision` (warning) — same 2-segment feature prefix, different path shape
  - identical re-registrations are explicitly NOT conflicts
- `engine/app/services/github_issues.py` — auto-issue action: `POST /repos/{owner}/{repo}/issues` with `GITHUB_TOKEN`, label `scaffold-conflict` (self-creating), deduped against identical open issues, fail-open in every failure mode
- `engine/app/services/conflict_recorder.py` — shared glue: writes `conflict_flagged` events + deduped open `blockers` rows, schedules issues fire-and-forget
- Wired into BOTH contract entry points: `POST /projects/:id/contracts` (detection before insert; still 201; additive `conflicts` response key) and `POST /projects/:id/github-webhook` (per-push `conflicts` count in `processed[]`)
- `engine/tests/test_day4b.py` — **25/25 green** (10 detection units + 5 issue-action units + DB-backed contracts-API and webhook sections against the real Supabase DB)
- `docs/demo-conflict-scenario.md` — the rehearsed §17 demo script (Agent A/B auth scenario, fallback paths, rehearsal checklist)

No schema changes — uses the existing `blockers` table and `conflict_flagged` event type, exactly as frozen Day 1.

Env vars added: `SCAFFOLD_GITHUB_REPO` (engine, optional — "owner/repo" for auto-filed issues, default `themdmohsin/Scaffold`); requires existing `GITHUB_TOKEN` for issue creation.

Still broken / not done: one live GitHub issue fire (needs `GITHUB_TOKEN` + `SCAFFOLD_GITHUB_REPO` in `engine/.env`), and the real two-machine loop with Part A's `tool.execute.before/after` hooks feeding `report_change` → contracts → this detector.

Next pair should start with: rehearse `docs/demo-conflict-scenario.md` end to end (suites verified 25/25 + 30/30 + 29/29 on 2026-09-25), then wire Part A's hooks.

---

# Day 3 — 2026-09-24 — Mohammed (solo)

## Reasoning + Retrieval + Realtime

Implemented the Day 3 reasoning and shared-context layer.

### Retrieval

Implemented:

`engine/app/services/retrieval.py`

Features:

- pgvector cosine-similarity retrieval
- project-scoped retrieval
- retrieval over `decisions`
- retrieval over `api_contracts`
- deterministic keyword/recency fallback
- graceful degradation when the embedding/LLM provider is unavailable

The retrieval layer selects relevant project context rather than sending the entire database to the LLM.

---

## Reasoning

Implemented:

`engine/app/services/reasoning.py`

This is the **only file responsible for LLM calls**.

It uses LiteLLM with Google Gemini.

Current chat model:

`gemini/gemini-3.6-flash`

Current embedding model:

`gemini/gemini-embedding-001`

Embedding dimension:

`1536`

The reasoning service provides:

- LLM chat completion
- embedding generation
- commit-summary generation
- `/reason` answer parsing
- defensive JSON handling

The `/reason` response follows the frozen structure:

```json
{
  "answer": "...",
  "suggested_tasks": [
    {
      "title": "...",
      "owner_id": null,
      "due_at": null
    }
  ]
}
````

---

## `/reason`

Implemented:

`POST /projects/:id/reason`

The route:

1. receives a user prompt
2. loads project context
3. retrieves relevant decisions/contracts
4. builds a bounded context block
5. sends that context to the reasoning model
6. returns the generated answer and suggested tasks

The context is intentionally bounded instead of sending the complete project database to every LLM request.

---

## Context Improvements

The previously empty context placeholders were implemented.

The project context now provides real:

* recent decisions
* relevant contracts

This also flows through the MCP `get_project_context()` functionality.

---

## Commit Summaries

`commits.summary` can now be generated by the LLM.

The behavior is fail-open:

* if the LLM succeeds, a summary is stored
* if the LLM is unavailable, the summary remains `null`

The deterministic diff parser remains responsible for deciding what actually changed.

The LLM is used for summarization and reasoning, not deterministic change detection.

---

# Day 3 Database Migration

Implemented:

`engine/app/db/migrate_day3.sql`

The migration:

* enables pgvector
* adds nullable `embedding VECTOR(1536)` columns
* creates HNSW indexes
* adds `tasks` to the Supabase Realtime publication
* adds `decisions` to the Supabase Realtime publication
* adds `events` to the Supabase Realtime publication

The migration is idempotent and is automatically applied by the engine startup process.

The migration was successfully applied against the real Supabase database.

---

# Embedding Backfill

Implemented:

`engine/app/scripts/backfill_embeddings.py`

The script is idempotent.

Run with:

```powershell
python -m app.scripts.backfill_embeddings
```

Re-embedding can be forced with:

```powershell
python -m app.scripts.backfill_embeddings --force
```

The actual backfill was successfully run.

Verified result:

```text
decisions: 3 embedded, 0 failed
contracts: 13 embedded, 0 failed
```

---

# Day 3 Dashboard

The dashboard was expanded into a functional project-management and shared-context interface.

Implemented:

### Task Board

* task creation
* task listing
* task status updates
* click-to-advance status

### Decision Log

Displays project decisions from the engine.

### API Contracts

Displays API contracts registered for the project.

### Ask Scaffold

The dashboard can:

* send prompts to `/reason`
* display the generated answer
* display suggested tasks
* create suggested tasks with one click

### Supabase Realtime

Added subscriptions for:

* tasks
* decisions
* events

Realtime updates trigger dashboard/engine refetches.

Realtime gracefully remains disabled when the required `VITE_SUPABASE_*` variables are not configured.

---

# Day 3 Verification

Day 3 automated test suite:

```powershell
python -m tests.test_day3
```

Result:

```text
29/29 green
```

Day 2 regression suite:

```powershell
python -m tests.test_day2
```

Result:

```text
30/30 green
```

Dashboard:

```powershell
npm run build
```

Result:

```text
successful
```

Engine health check:

```text
/health
```

Result:

```text
successful
```

---

# Real `/reason` Verification

A Google AI Studio API key was configured locally in:

`engine/.env`

The initial Day 3 chat model was:

`gemini/gemini-2.5-flash`

That model became unavailable through the provider.

The chat model was updated to:

`gemini/gemini-3.6-flash`

The embedding model remained:

`gemini/gemini-embedding-001`

A real request was made against:

`POST /projects/fdcb7511-434b-40c1-a391-0cd45e2150a5/reason`

Prompt:

```text
What API contracts currently exist in this project, and what should I be aware of before adding a new payment-related endpoint?
```

The request returned:

```text
HTTP 200
```

The response successfully contained:

* an AI-generated answer
* existing API contract information
* suggested tasks

This confirmed that the live reasoning path is working:

```text
Project
   ↓
Context
   ↓
Retrieval
   ↓
Gemini
   ↓
/reason
   ↓
Answer + Suggested Tasks
```

---

# Day 2 — 2026-09-24 — Mohammed (solo)

## Ingestion + MCP

Implemented and verified the Day 2 ingestion layer.

---

## GitHub Webhook

Implemented:

`POST /projects/:id/github-webhook`

The webhook includes:

* HMAC-SHA256 signature verification
* fail-closed behavior when the webhook secret is missing
* invalid signature rejection
* malformed/missing payload handling
* GitHub push event handling
* GitHub REST API integration
* commit/diff retrieval
* deterministic diff parsing
* commit ingestion
* API contract ingestion
* event logging

---

## Diff Parser

Implemented:

`engine/app/services/diff_parser.py`

The parser is deterministic.

It extracts new facts from Git diffs, including:

* API routes
* dependencies
* environment variables

It supports relevant FastAPI, Express, and Flask route patterns.

Dependency extraction covers:

* `requirements.txt`
* `pyproject.toml`
* `package.json`

Environment-key extraction covers supported environment template files.

Only added lines are treated as newly introduced facts.

The LLM is not responsible for deciding whether something changed.

---

# Supabase Ingestion

The GitHub webhook writes information into Supabase.

The ingestion flow is:

```text
GitHub Push
    ↓
GitHub Webhook
    ↓
FastAPI
    ↓
GitHub REST API
    ↓
Diff Parser
    ↓
commits
api_contracts
events
```

---

# API Contracts

Implemented:

```text
GET  /projects/:id/contracts
POST /projects/:id/contracts
```

Contract creation validates the project and optional task association.

Contract registration generates an event.

API contracts are also used by the Day 3 retrieval/reasoning system.

---

# Decisions

Implemented:

```text
GET  /projects/:id/decisions
POST /projects/:id/decisions
```

Decision creation generates the appropriate event and participates in Day 3 embedding/retrieval.

---

# MCP Server

Implemented:

`/mcp`

as a streamable HTTP MCP server.

The six frozen MCP tool names were implemented against real Postgres.

Tools support an optional:

`project_id`

and can fall back to:

`SCAFFOLD_DEFAULT_PROJECT_ID`

when the project ID is omitted.

The MCP tools were tested through the real MCP protocol:

```text
initialize
    ↓
tools/list
    ↓
tools/call
```

against a running real uvicorn server.

---

# Day 2 Verification

Automated test suite:

```powershell
python -m tests.test_day2
```

Result:

```text
30/30 green
```

The Day 2 suite continues to pass after the Day 3 changes.

---

# Real GitHub Webhook Verification

The real GitHub webhook test was completed.

The engine was made publicly reachable through ngrok.

The GitHub webhook was configured for:

```text
/projects/fdcb7511-434b-40c1-a391-0cd45e2150a5/github-webhook
```

A real GitHub push was performed.

Verified flow:

```text
GitHub
   ↓
ngrok
   ↓
Scaffold FastAPI Engine
   ↓
GitHub API
   ↓
diff_parser.py
   ↓
Supabase
```

The resulting commit and API contract information appeared in Supabase.

Real test routes observed through the webhook flow included:

```text
/api/test/scaffold
/api/test/scaffold-v2
```

This closes the Day 2 real-webhook carryover.

---

# Day 1 — 2026-09-23 — Mohammed (solo, both tracks)

## Engine

Built the initial Scaffold Engine using FastAPI.

Implemented:

```text
GET /health
POST /projects
GET /projects/:id/context
```

and full task CRUD.

The engine was verified end-to-end against live Supabase.

The primary development/test project is:

```text
fdcb7511-434b-40c1-a391-0cd45e2150a5
```

Initial verification included:

* create
* list
* patch
* event logging
* 400 validation
* 404 validation
* 422 validation

---

# Supabase

The initial database schema was applied through the Supabase SQL editor.

The database contains tables for:

* projects
* users
* tasks
* task dependencies
* decisions
* events
* commits
* API contracts
* blockers
* related project state

Day 3 later added pgvector and Supabase Realtime requirements.

---

# OpenCode Fork

The OpenCode fork was vendored under:

`opencode-plugin/opencode/`

The fork runs from source through Bun.

Visible Scaffold rebranding was implemented, including:

* Scaffold terminal branding
* Scaffold terminal titles
* Scaffold TUI home logo/banner

The visible branding was changed while the underlying OpenCode package/binary identity was not completely renamed.

---

# Initial Scaffold Plugin

Initial plugin skeleton:

`opencode-plugin/.opencode/plugins/scaffold.ts`

The Day 1 plugin contained log-only hooks.

It was installed globally so that it could load in projects.

The actual engine-aware plugin integration is deferred to Day 4.

---

# Initial Dashboard

Created the initial dashboard using:

```text
Vite
React
TypeScript
```

The initial dashboard provided the project join flow.

Day 3 expanded this into:

* task board
* decision log
* contracts view
* Ask Scaffold panel
* Realtime functionality

---

# Frozen Documentation

The initial frozen documentation includes:

```text
docs/SCHEMA.md
docs/API_CONTRACTS.md
```

These documents define the core database/API shapes and integration boundaries.

The API contract document also records the verified OpenCode hook API needed for the later plugin integration.

---

# Current Architecture

The current architecture is:

```text
                       ┌──────────────┐
                       │    GitHub    │
                       └──────┬───────┘
                              │
                         Push Webhook
                              │
                              ▼
                       ┌──────────────┐
                       │    ngrok     │
                       └──────┬───────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │  Scaffold Engine   │
                    │      FastAPI       │
                    └─────────┬──────────┘
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
       Diff Parser        Retrieval         Reasoning
       deterministic       pgvector          Gemini
             │                │                │
             └────────────────┼────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │     Supabase     │
                    ├──────────────────┤
                    │ projects         │
                    │ tasks            │
                    │ decisions        │
                    │ contracts        │
                    │ commits          │
                    │ events           │
                    │ embeddings       │
                    └────────┬─────────┘
                             │
                             ▼
                       ┌───────────┐
                       │ Dashboard │
                       └───────────┘
```

The next layer adds the OpenCode agent:

```text
                    Scaffold Engine
                           ▲
                           │
                     shared context
                           │
                           ▼
                     OpenCode Fork
                           │
                    ┌──────┴──────┐
                    │             │
                 Agent A       Agent B
                    │             │
                    └──────┬──────┘
                           │
                       coding work
```

---

# Important Environment Variables

The project uses environment variables including:

```text
SUPABASE_URL
SUPABASE_SERVICE_KEY
SCAFFOLD_TEAM_LLM_KEY
GITHUB_WEBHOOK_SECRET
GITHUB_TOKEN
DATABASE_URL
```

Optional variables include:

```text
SCAFFOLD_DEFAULT_PROJECT_ID
SCAFFOLD_LLM_MODEL
SCAFFOLD_EMBED_MODEL
```

The OpenCode plugin uses:

```text
SCAFFOLD_ENGINE_URL
```

Actual secrets must remain in local `.env` files and must never be committed to GitHub.

---

# Repository Rules

## Rule #1 — LLM Boundary

Only:

`engine/app/services/reasoning.py`

should directly call the LLM provider.

Other services should use the reasoning layer.

---

## Rule #2 — Deterministic Change Detection

Git diffs decide what actually changed.

The diff parser is deterministic.

The LLM may summarize changes but must not decide whether a route, dependency, or environment key was actually added.

---

## Rule #3 — Retrieval Fallback

Retrieval should degrade gracefully.

If the embedding provider is unavailable, deterministic keyword/recency retrieval should still provide useful project context.

---

## Rule #6 — Bounded Context

The reasoning model should receive a targeted, bounded context block.

Do not send the entire project database to every LLM request.

---

# Current Verified State

```text
Day 1 implementation        COMPLETE
Day 2 implementation        COMPLETE
Day 3 implementation        COMPLETE
Day 4 Part A (plugin hooks) COMPLETE — offline-verified only
Day 4 Part B (conflicts)    COMPLETE — engine side

Day 2 tests                 30/30 PASS
Day 3 tests                 29/29 PASS
Day 4A plugin suite         55/55 PASS
Day 4A real-SDK interop     17/17 PASS
Day 4A acceptance self-test 4/4 PASS
Day 4B suite                25/25 PASS
Dashboard build             PASS
Engine health               PASS
Database migration          PASS
Embedding backfill         PASS
Real GitHub webhook         PASS
Real /reason                PASS
Live two-machine Day 4 loop OPEN
```

---

# Day 4 — Next Milestone

> **UPDATE 2026-09-25:** Day 4 is DONE — Part A (plugin hooks) and Part B (conflict detection)
> are both merged to main. The next-person steps below are historical; see the Current State
> banners at the top of this file and the Day 4 entries near the end.

The next major milestone is **OpenCode Plugin Integration**.

The goal is to connect the existing OpenCode fork to the Scaffold Engine.

Expected flow:

```text
OpenCode
   │
   │ tool.execute.before
   ▼
Scaffold Plugin
   │
   ▼
get_project_context
get_api_contract
   │
   ▼
Inject relevant Scaffold context
   │
   ▼
Agent executes tool
   │
   │ tool.execute.after
   ▼
Scaffold Plugin
   │
   ▼
report_change
   │
   ▼
Scaffold Engine
   │
   ▼
Supabase / shared project state
```

The purpose is to make the coding agent aware of shared project state before it acts and report meaningful changes after it acts.

---

# Day 4 Demonstration Goal

The intended demonstration is that multiple coding agents can work from a common project context rather than operating as isolated agents.

Example:

```text
Agent A
   ↓
makes change
   ↓
Scaffold records change
   ↓
shared project context updated
   ↓
Agent B
   ↓
retrieves current context
   ↓
continues work with the updated information
```

This leads into the conflict-prevention demonstration from the project plan.

---

# Important: Do Not Restart Previous Days

Day 1, Day 2, and Day 3 are implemented and verified.

Do not redo completed work unless a regression is discovered.

The next agent/person should start from **Day 4**.

Immediate next steps:

1. Inspect the existing OpenCode plugin.
2. Implement `tool.execute.before`.
3. Inject relevant Scaffold context before agent actions.
4. Implement `tool.execute.after`.
5. Report relevant changes back to the Scaffold Engine.
6. Test the integration with a real OpenCode coding session.
7. Rehearse the multi-agent conflict-prevention demonstration.

---

# End State

Scaffold currently provides the foundation for:

```text
Project
   ↓
Shared database
   ↓
GitHub ingestion
   ↓
Deterministic change detection
   ↓
API contract tracking
   ↓
Decision tracking
   ↓
Vector retrieval
   ↓
LLM reasoning
   ↓
Suggested tasks
   ↓
Dashboard
   ↓
MCP
```

The next step is to connect the actual coding agents to this shared brain through the OpenCode plugin.

````

**Yes — that whole block is what goes into `HANDOFF.md`.** Then save it and push only that documentation update:

```powershell
git add HANDOFF.md
git commit -m "docs: update handoff through day 3"
git push origin main
````

---

## Day 4 — 2026-09-25 — Section A (OpenCode plugin hooks)

Built: the plugin loop is wired. `opencode-plugin/.opencode/plugins/scaffold.ts` now speaks
MCP (streamable HTTP JSON-RPC at `<SCAFFOLD_ENGINE_URL>/mcp`, initialize handshake + session
id, JSON and SSE replies, 2.5s timeout, 60s backoff after repeated failures) and drives four
hooks: `experimental.chat.system.transform` injects the bounded `get_project_context()` block
(2400 chars, refreshed on every user message, 5 decisions / 5 contracts / 8 tasks);
`tool.execute.before` finds `/api/...` routes inside `write`/`edit`/`apply_patch` arguments and
pins `get_api_contract(route)` into the next request; `tool.execute.after` calls
`report_change(diff_summary, files_changed)` built deterministically from the tool result's
real additions/deletions; `experimental.session.compacting` keeps the block through
compaction. Verified with `cd opencode-plugin && node tests/verify_plugin.ts` — 55/55 checks
against a fake wire-faithful engine (including engine-down and timeout paths) — and
`python opencode-plugin/tests/mcp_sdk_interop.py` — 17/17 against the *real* `mcp` SDK server
using the engine's own mount code with fixture tools (no Postgres, no LLM), which proves the
plugin's hand-rolled MCP client speaks the same wire format the engine serves. The plugin and its
harnesses also typecheck strictly against the vendored `@opencode-ai/plugin` types
(`cd opencode-plugin && npm install && npm run typecheck`) — the negative control was verified to
fail, so that check is not vacuous. `python opencode-plugin/tests/acceptance_live.py` is the
Day 4 acceptance runner: pointed at a live engine it prints the exact block an agent receives
(`--self-test` proves the runner itself without an engine).

A hardening pass over the plugin then fixed three real defects: a failed `get_project_context`
fetch used to be cached as fresh-but-empty for the whole 20s TTL (which killed context injection
*and* contract pinning until the next user message); session start logged one generic line
whether the engine was unreachable or the project was simply unseeded (now distinct warns and
toasts); and `write` summaries counted UTF-16 code units while labelling them "bytes" (now
`Buffer.byteLength`, asserted against multibyte content). The caching fix was proven with a
deliberate reintroduction of the old condition — the three new regression checks failed loudly
under it and pass after the revert.

A second pass made reporting durable: `report_change` payloads the engine cannot accept (it is
down, or up but erroring per call) are parked in a bounded queue (max 50, kept 1h) and replayed
oldest-first at the next session start or successful report — a write is never silently lost to a
transient outage. The MCP client also re-handshakes once when the engine answers 404 for a stale
session id (what a restarted engine does) and rejects any 2xx body that is not a JSON-RPC reply
for the outstanding request. The replay behaviour was proven the same way: with the queue
deliberately disabled the three new regression checks fail loudly, and pass after the revert.
`verify_plugin.ts` is now 55 checks.

Env vars added: none. The plugin deliberately sends no `project_id`; the engine applies
`SCAFFOLD_DEFAULT_PROJECT_ID` (the convention already frozen on Day 2).

Still broken / not done: not tested in a real OpenCode session against a real
Postgres-backed engine — this machine has no `engine/.env`, no Supabase credentials and no bun,
so the live acceptance runner and the interactive session below have never been executed for real. `report_change` only fires for file-writing tools; changes made by a shell
command (a `git commit`, a formatter) are not reported. Contract pinning is lookup-only — it
surfaces the conflict *to the agent* in the prompt, it does not block the write (that is
Person B's conflict-prevention work). Day 4's own handoff requirement — the full loop across
two separate machines — is still open.

Next pair should start with: run the plugin in a real coding session on two laptops against a
deployed engine, and watch whether Dev B's next prompt actually carries Dev A's change.

---

## Day 5 — 2026-09-26 — Person A (task assignment + teammate invites)

Built (offline-verified, `python -m tests.test_day5` — 33/33 pure checks):

- `engine/app/services/availability.py` — the deterministic core, no LLM, no network:
  `compute_roster` (open-task load per user, sorted by load then name, bounded at 10),
  `hours_until`, `render_roster_block` (the TEAM ROSTER block listing verbatim user_ids),
  `validate_assignments` (unknown owner_id -> null; past/unparseable due_at -> null;
  due_at beyond the project deadline -> clamped; returns notes for every correction), and a thin
  `roster(db, project_id)` DB wrapper using a real SQL GROUP BY.
- `/reason` extended, shape frozen: the context block gains the TEAM ROSTER section; the system
  prompt now permits owner_id/due_at but ONLY copied verbatim from the roster; every suggestion
  is re-validated AFTER the LLM before it reaches the client. Additive `assignment_notes` key
  appears only when something was corrected. Fail-open everywhere.
- `engine/app/routes/invites.py` — `POST /projects/{id}/invite` -> `{invite_url, code,
  expires_at_epoch}` and `POST /projects/join` `{code, name, role?}` -> 201 creates the
  `users` row + `teammate_joined` event. Stateless HMAC-SHA256 codes
  (`project_id.expiry.signature`, 7-day TTL) signed with the existing GITHUB_WEBHOOK_SECRET —
  no invites table, no schema changes, no new env vars (503 if the secret is unset). This is
  the first API that can create users; previously the two-laptop test needed manual SQL seeding.
- `engine/tests/test_day5.py` — pure units for roster math, deadline math, roster rendering and
  every validation rule, plus full invite-code tamper/expiry/malformation coverage; DB-backed
  route sections (TestClient, Day 2/3/4b style) are included and SKIP loudly without
  DATABASE_URL. Validation rules proven by negative control (validator disabled -> exactly the
  5 validation checks fail -> reverted).
- A self-audit pass then fixed four defects before they could ship: a naive (offset-less)
  deadline crashed `hours_until`/`render_roster_block`/clamping with TypeError (all datetimes
  now normalized through one `_as_utc` helper), the `compute_roster` docstring promised a
  nonexistent `has_deadline_task` field, `roster()` carried a dead `now` parameter, and re-joining
  with the same name created duplicate `users` rows (now idempotent: same name -> same user,
  `"existing": true`, 200). Each fix proven by red-then-green regression checks; the crash fix
  additionally by negative control (normalization disabled -> exactly the 3 naive-datetime
  checks fail -> reverted).
- A second audit round fixed four more: `/reason` crashed with TypeError when an LLM suggested a
  non-string owner_id (unhashable dict -> set membership check) — now only strings are compared;
  an uppercase-but-valid UUID was cleared instead of accepted (owner ids now normalized
  case/whitespace before comparison, canonical roster spelling kept — LLM-mangled UUIDs are
  common); the context renderer's 4000-char budget was a hardcoded constant (now an explicit
  `max_chars` parameter, default unchanged); and `answer_prompt` still carried the dead `roster`
  parameter from the first draft (removed). Round-2 regressions proven red-then-green; the
  owner-normalization fix proven by negative control (strict membership restored -> exactly 2
  checks fail -> reverted).
- A third audit round fixed four more: a non-string title in an LLM suggestion raised TypeError
  (titles are now validated — junk entries dropped with a note); the `/reason` context was
  combined AFTER truncation, so the roster block could push the payload past the 4000-char rule
  #6 budget (the always-on summary now yields room to the roster inside one bounded block); the
  join dedup was case-SENSITIVE at the SQL level (`"dev b"` re-joining after `"Dev B"` planted a
  duplicate row — now `func.lower` comparison, a fix the offline suite pins by source-inspection
  since the DB leg cannot run here, proven by negative control); and the unused `InviteBody`
  model was removed. Round-3 regressions proven red-then-green; `python -m tests.test_day5` is
  now 48 checks (including one source-pinned check that exists precisely because the DB leg is
  hardware-gated).
- A fourth audit round fixed three more and re-made two of its own checks honest: `compute_roster`
  still carried the dead now/deadline params (removed — the round-3 standard applied consistently);
  and a join name containing a newline or tab would become a forged line inside the LLM TEAM
  ROSTER block — an injection vector straight into every agent's prompt (all join labels are now
  whitespace-collapsed via `_sanitize_label`). The round-4 guardrails themselves were caught being
  vacuous — a changelog-sync check passing on an unrelated word, and a teardown check finding its
  own sentinel — both re-written to be falsifiable, which exposed two REAL gaps: the API_CONTRACTS
  entry did not document the idempotent 200/`existing` join semantics (now documented), and the
  DB-backed route tests polluted the demo database with day5 rows (now best-effort DELETE
  teardown so the runbook machine stays clean). `python -m tests.test_day5` is now 53 checks.

Still broken / not done: Person B's Day 5 two-laptop integration pass with the
honest go/no-go on the Section 17 demo. The DB-backed route legs and the live-LLM
assignment path were verified on hardware the same day (see the top banner; `engine/.env` now
exists locally and `max_tokens` was raised 800 → 2000 after the live run exposed Gemini 3
thinking-token truncation).

Next pair should start with: on a machine with engine/.env, run `python -m tests.test_day5`
end to end (route legs included), then the two-laptop pass.

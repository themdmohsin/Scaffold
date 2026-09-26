# HANDOFF

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

Day 2 tests                 30/30 PASS
Day 3 tests                 29/29 PASS
Dashboard build             PASS
Engine health               PASS
Database migration          PASS
Embedding backfill         PASS
Real GitHub webhook         PASS
Real /reason                PASS
```

---

# Day 4 — Next Milestone

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

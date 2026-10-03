# AGENTS.md — Scaffold Project

Instructions for any AI coding agent (Codex, Cursor, Windsurf, Claude Code via CLAUDE.md, etc.) working in this repo. Read this before writing any code. Do not deviate from the naming/contracts below without a human explicitly approving the change — they are frozen per `docs/API_CONTRACTS.md` and `docs/SCHEMA.md`.

## Project

Scaffold — a coding interface (forked from OpenCode) that gives teammates' AI coding sessions live shared awareness of the project (tasks, decisions, API contracts) so agents stop inventing conflicting designs. Full architecture: `docs/scaffold-technical-research.md`. Build plan: `docs/scaffold-build-plan.md`.

## Repo structure — do not create new top-level folders

```
engine/            FastAPI backend
  app/main.py
  app/db/          schema.sql, models.py
  app/routes/       tasks.py, decisions.py, context.py, github_webhook.py
  app/services/     retrieval.py, reasoning.py, diff_parser.py
  app/mcp_server.py
dashboard/          React + Vite frontend
opencode-plugin/     the OpenCode fork + hook plugin
docs/               SCHEMA.md, API_CONTRACTS.md, HANDOFF.md
```

## Frozen contracts — use these exact names, never invent your own

**Database tables** (Postgres/Supabase): `projects, users, tasks, task_dependencies, decisions, decision_affects_tasks, api_contracts, commits, blockers, events`.
Phase 6 additions: `accounts, project_members, personal_access_tokens, invites, invite_redemptions` (+ `users.account_id`). Full column definitions in `docs/SCHEMA.md` — read it before writing any query or model.

**API routes** (`engine/`):
```
GET   /projects/:id/context
GET   /projects/:id/tasks
POST  /projects/:id/tasks
PATCH /projects/:id/tasks/:task_id
GET   /projects/:id/decisions
POST  /projects/:id/decisions
GET   /projects/:id/contracts
POST  /projects/:id/contracts
POST  /projects/:id/github-webhook
POST  /projects/:id/reason
GET   /projects/:id/tasks/ready
GET   /projects/:id/recommendations
GET   /projects/:id/recommendations/next
POST  /projects/:id/recommendations/next
GET   /projects/:id/coordination
POST  /projects/:id/tasks/:task_id/accept-recommendation
POST  /projects/:id/tasks/:task_id/reject-recommendation
```

**Phase 6 auth additions** (real authentication — see `docs/API_CONTRACTS.md` "Authentication & authorization"):
```
GET    /auth/me
POST   /auth/tokens
GET    /auth/tokens
DELETE /auth/tokens/:token_id
GET    /projects
GET    /projects/:id/invites
DELETE /projects/:id/invites/:invite_id
```
`POST /projects` now creates the project owned by the caller; `POST /projects/join` and `POST /projects/:id/invite` stay as-frozen but are JWT/admin-gated. **Every route and MCP tool requires `Authorization: Bearer <Supabase JWT or scaffold_ PAT>`; identity is never read from a body/query** (legacy `requesting_user_id`/`user_id` fields are accepted-but-ignored). 401 unauthenticated, 403 non-member/role too low, 503 when `SUPABASE_JWT_SECRET` is unset (fail closed).

**MCP tool names** (`engine/app/mcp_server.py`) — the OpenCode plugin calls these verbatim, do not rename:
```
get_project_context()
get_api_contract(route: str)
get_active_tasks()
get_recent_decisions()
report_change(diff_summary: str, files_changed: list[str])
create_task(title: str, owner_id: str | None, due_at: str | None)
get_ready_tasks()
get_recommended_task(user_id: str | None)
```

**Env vars** — use exactly these names, add new ones to `.env.example` in the same commit if you introduce one:
```
SUPABASE_URL, SUPABASE_SERVICE_KEY, SCAFFOLD_TEAM_LLM_KEY,
GITHUB_WEBHOOK_SECRET, GITHUB_TOKEN,
SUPABASE_JWT_SECRET, SCAFFOLD_CORS_ORIGINS, SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS
VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_ENGINE_URL
SCAFFOLD_ENGINE_URL, SCAFFOLD_TOKEN
```
(`SUPABASE_JWT_SECRET` verifies Supabase Auth JWTs — required, else 503 fail-closed. `SCAFFOLD_CORS_ORIGINS` = comma-separated browser origins, empty = wildcard dev-only. `SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS` = DEV/SEED only, auto-promotes listed Supabase user ids to owner. `SCAFFOLD_TOKEN` = the plugin's PAT, sent as `Authorization: Bearer` on every MCP call.)

## Tech stack — do not substitute without asking a human

- Backend: FastAPI (Python)
- DB: Postgres via Supabase, pgvector extension for embeddings — no separate vector DB, no graph DB
- Realtime: Supabase Realtime
- Frontend: React + Vite
- Multi-model calls inside the engine: LiteLLM only, and only in `engine/app/services/reasoning.py` — no other file should call an LLM provider directly
- Client: forked OpenCode — do not build a new coding-agent loop from scratch

## Hard rules

1. **`SCAFFOLD_TEAM_LLM_KEY` pays for the engine's own internal reasoning calls only** (retrieval compression, diff summarization, the `/reason` endpoint). It must never be used for a developer's own coding session — that goes through OpenCode's native provider routing using the developer's own configured key. Do not wire these together.
2. **Git diffs are the source of truth for "what changed," never an LLM's self-report.** Use `diff_parser.py` (regex/string matching) to extract new routes, new dependencies, new env keys. An LLM call may *summarize* a diff into one sentence; it must never be the thing that *decides* what changed.
3. **Deterministic logic stays deterministic.** Bottleneck detection, "who's free," "hours until deadline," and similar are plain SQL/Python — never route these through an LLM call.
4. **Full source file contents are never written to the database.** Store file paths + diff summaries only — Git already stores the code.
5. **Don't attempt real Git merge-conflict resolution.** Out of scope. Scaffold prevents *semantic* conflicts (two agents inventing different API shapes for the same feature) by serving the real contract before code is written — it does not resolve line-level Git merges.
6. **Context sent to any LLM call should be small and targeted** — the always-on project summary (~1-2K tokens) plus specifically retrieved context for the current request, never a full project dump.

## Workflow

- Branch naming: `day<N>-<initials>-<short-feature>`, e.g. `day2-mk-webhook`
- Small, frequent commits, plain messages
- PR into `main` same day — don't let branches live more than a day
- Pull `main` before starting any session
- At the end of any work session, append a short entry to `docs/HANDOFF.md`: what's built, any new env vars, what's still broken, what the next person should do first

## Build / test

```
# engine
cd engine && pip install -r requirements.txt && uvicorn app.main:app --reload
# engine smoke test (no server needed)
cd engine && python -c "from starlette.testclient import TestClient; from app.main import app; print(TestClient(app).get('/health').json())"
# engine seed (requires engine/.env with DATABASE_URL + schema applied)
cd engine && python -m app.scripts.seed
# engine backfill embeddings (requires SCAFFOLD_TEAM_LLM_KEY; idempotent)
cd engine && python -m app.scripts.backfill_embeddings

# dashboard
cd dashboard && npm install && npm run dev
# dashboard typecheck + production build
cd dashboard && npm run build

# opencode fork (the Scaffold client) — run from source
cd opencode-plugin/opencode && bun install --ignore-scripts
bun run --cwd packages/opencode src/index.ts --version   # -> local

# engine verification harnesses (require engine/.env with DATABASE_URL + schema applied)
cd engine && python -m tests.test_day2   # diff parser, signed webhook, all 6 MCP tools
cd engine && python -m tests.test_day3   # retrieval, /reason, context enrichment
cd engine && python -m tests.test_day4b  # conflict detection + auto-issue
cd engine && python -m tests.test_day5   # assignment, invites/join, context additions
cd engine && python -m tests.test_phase2 # task board: deps, blockers, activity feed
cd engine && python -m tests.test_phase3 # team: roster, agents, ownership
cd engine && python -m tests.test_phase4 # ready tasks, recommendations, next action, overrides
cd engine && python -m tests.test_phase5 # secure environment: metadata, grants, secret store, non-disclosure
cd engine && python -m tests.test_auth   # Phase 6: JWT/PAT credentials, 401/403/role gates, invites, RLS

# plugin typecheck (vendored @opencode-ai/plugin types; npm here is only for tsc)
cd opencode-plugin && npm install && npm run typecheck
# plugin verification harnesses
# (no engine, no Postgres, no LLM needed; Node 22.18+ strips TS types natively)
cd opencode-plugin && node tests/verify_plugin.ts
cd opencode-plugin && python tests/mcp_sdk_interop.py        # vs the real mcp SDK (engine/.venv)
cd opencode-plugin && python tests/acceptance_live.py --self-test
# plugin acceptance against a LIVE engine (needs engine/.env + Postgres up; no LLM key)
# SCAFFOLD_TOKEN (a PAT from POST /auth/tokens) is REQUIRED — /mcp is Bearer-gated
cd opencode-plugin && SCAFFOLD_TOKEN=scaffold_... python tests/acceptance_live.py
```
(There is no pytest suite: the engine harnesses are script-style modules that print PASS/FAIL and exit non-zero on failure. The dashboard is checked via `npm run build`. Keep this section current when harnesses move.)

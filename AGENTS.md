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

**Database tables** (Postgres/Supabase): `projects, users, tasks, task_dependencies, decisions, decision_affects_tasks, api_contracts, commits, blockers, events`. Full column definitions in `docs/SCHEMA.md` — read it before writing any query or model.

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
```

**MCP tool names** (`engine/app/mcp_server.py`) — the OpenCode plugin calls these verbatim, do not rename:
```
get_project_context()
get_api_contract(route: str)
get_active_tasks()
get_recent_decisions()
report_change(diff_summary: str, files_changed: list[str])
create_task(title: str, owner_id: str | None, due_at: str | None)
```

**Env vars** — use exactly these names, add new ones to `.env.example` in the same commit if you introduce one:
```
SUPABASE_URL, SUPABASE_SERVICE_KEY, SCAFFOLD_TEAM_LLM_KEY,
GITHUB_WEBHOOK_SECRET, GITHUB_TOKEN
VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_ENGINE_URL
```

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

# dashboard
cd dashboard && npm install && npm run dev
# dashboard typecheck + production build
cd dashboard && npm run build

# opencode fork (the Scaffold client) — run from source
cd opencode-plugin/opencode && bun install --ignore-scripts
bun run --cwd packages/opencode src/index.ts --version   # -> local
```
(No automated test suite yet — engine is smoke-tested via TestClient, dashboard via `npm run build`. Update this section when real tests land.)

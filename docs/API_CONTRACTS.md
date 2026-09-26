# API_CONTRACTS.md — Scaffold Engine Contract

> **FROZEN 2026-09-23 (Day 1).** This file is **append-only** after Day 1. Any change needs a one-line note to the team and a changelog entry at the bottom.

Base: the `engine/` FastAPI service (`VITE_ENGINE_URL` for clients, `SCAFFOLD_ENGINE_URL` for the plugin). All bodies are JSON. Errors use FastAPI's default shape: `{"detail": "..."}` with proper 4xx/5xx status.

## Routes (Day 1 freeze)

### `GET /health`
Liveness probe. `200 {"status": "ok"}` — no auth, no DB dependency.

### `POST /projects` *(Day 1 addition — needed to create projects before they have IDs; used by every later day)*
```jsonc
// request
{ "name": "required", "goal": "optional", "deadline": "optional ISO-8601 timestamp" }
// response 201
{ "id": "<uuid>", "name": "...", "goal": null, "deadline": null, "created_at": "..." }
```

### `GET /projects/:id/context`
Returns the always-on summary (master doc §7). Shape stays frozen; contents fill in Day 3.
```jsonc
// response 200
{
  "project": { "id": "...", "name": "...", "goal": null, "deadline": null },
  "tasks": { "todo": 0, "in_progress": 0, "done": 0 },
  "active_tasks": [ /* up to 10 non-done tasks: { id, title, status, owner_id, due_at } */ ],
  "recent_decisions": [ /* newest first, max 5: { id, text, created_at } — populated Day 3 */ ],
  "relevant_contracts": [ /* { route, method } — populated Day 3 */ ],
  "generated_at": "ISO-8601"
}
```
`404` if the project doesn't exist.

### `GET /projects/:id/tasks`
`200 [ { "id", "title", "status", "owner_id", "due_at", "created_at" } ]` — ordered by `created_at` ascending. `404` unknown project.

### `POST /projects/:id/tasks`
```jsonc
// request
{ "title": "required", "owner_id": "optional uuid", "due_at": "optional ISO-8601" }
// response 201 — the full task object (same shape as GET list items)
```
`404` unknown project, `400` missing title or invalid `owner_id`/`due_at`. Writes an `events` row of type `task_created`.

### `PATCH /projects/:id/tasks/:task_id`
```jsonc
// request — one or both of
{ "status": "todo" | "in_progress" | "done", "owner_id": "optional uuid" }
// response 200 — the full updated task object
```
`404` unknown project or task, `400` invalid status value. Writes an `events` row of type `task_updated`.

### `GET /projects/:id/decisions`
`200 [ { "id", "text", "reasoning", "made_by", "created_at" } ]` — newest first. *(Route frozen Day 1; implemented Day 2.)*

### `POST /projects/:id/decisions`
```jsonc
// request
{ "text": "required", "reasoning": "optional", "made_by": "optional uuid" }
// response 201 — the full decision object
```
*(Route frozen Day 1; implemented Day 2.)* Writes an `events` row of type `decision_logged`.

### `GET /projects/:id/contracts`
`200 [ { "id", "route", "method", "request_schema", "response_schema", "created_by_task_id", "created_at" } ]` — newest first. *(Route frozen Day 1; implemented Day 2.)*

### `POST /projects/:id/contracts`
```jsonc
// request
{ "route": "required", "method": "required", "request_schema": {}, "response_schema": {}, "created_by_task_id": "optional uuid" }
// response 201 — the full contract object
```
*(Route frozen Day 1; implemented Day 2 — this is what the plugin hook calls, master doc §4 step 7.)*

### `POST /projects/:id/github-webhook`
GitHub push/PR receiver. Signature-verified with `GITHUB_WEBHOOK_SECRET` (HMAC-SHA256 of the raw body in `X-Hub-Signature-256`). `200 {"ok": true}` on accepted events. *(Route frozen Day 1; implemented Day 2.)*

### `POST /projects/:id/reason`
```jsonc
// request
{ "prompt": "required" }
// response 200
{ "answer": "...", "suggested_tasks": [ { "title": "...", "owner_id": null, "due_at": null } ] }
```
The ONLY route that calls an LLM for a user-facing answer (via LiteLLM, `SCAFFOLD_TEAM_LLM_KEY`, in `services/reasoning.py` only). *(Route frozen Day 1; implemented Day 3.)*

## MCP server (Day 2 — LIVE at `/mcp`, streamable HTTP)
The OpenCode plugin calls these verbatim — do not rename. All tools hit real Postgres; deterministic logic only (repo rule #3). Every tool takes an optional `project_id`; when omitted the server uses `SCAFFOLD_DEFAULT_PROJECT_ID` (engine .env) — the single-project demo convention.
```
get_project_context(project_id?)                                    → context object (same shape as GET /context)
get_api_contract(route: str, project_id?)                           → { found, method, request_schema, response_schema } | { found: false }
get_active_tasks(project_id?)                                       → { tasks: [...], count }   (non-done, oldest first)
get_recent_decisions(limit? = 5, project_id?)                       → { decisions: [...], count }
report_change(diff_summary: str, files_changed: list[str], project_id?) → { ok, event_id }   (writes change_reported event)
create_task(title: str, owner_id? = null, due_at? = null, project_id?)  → { id, title, status }  (writes task_created event)
```
Wire into any MCP client: endpoint `http://<engine>/mcp` (streamable HTTP).

## Environment variables (exact names — `.env.example` mirrors these)

```
# engine/.env
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
SCAFFOLD_TEAM_LLM_KEY=          # the team's own key, LiteLLM uses this (master doc §10)
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=                   # PAT fallback if GitHub App setup stalls
DATABASE_URL=                   # Day 1 addition — Postgres pooler connection string so the engine can reach Supabase

# dashboard/.env
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_ENGINE_URL=

# opencode-plugin/.env (Day 1 addition — new component)
SCAFFOLD_ENGINE_URL=            # where the plugin's hooks reach the engine
```

## OpenCode plugin hook API (verified 2026-09-23 against opencode.ai/docs/plugins)

Plugins are JS/TS modules loaded from `.opencode/plugins/` (project) or `~/.config/opencode/plugins/` (global), or npm packages listed in `opencode.json`. A plugin exports one or more async functions receiving context `{ project, client, $, directory, worktree }` and returning a hooks object. TypeScript type: `Plugin` from `@opencode-ai/plugin`.

Hooks relevant to Scaffold:

| hook | fires | we use it for (Day 4) |
| --- | --- | --- |
| `tool.execute.before` | before each tool call; `{ tool, sessionID, callID }` + mutable `output.args` | inject project context before write tools |
| `tool.execute.after` | after each tool call; `output` carries the result | report changes back to the engine |
| `event` (session events) | `session.created`, `session.idle`, `session.error`, `session.diff`, `session.compacted`, `session.updated`, ... | session lifecycle awareness |
| `message.updated` / `message.part.updated` | message stream changes | detect user prompts |
| `file.edited` / `file.watcher.updated` | file changes | change detection signal |
| `shell.env` | shell env setup | inject `SCAFFOLD_*` vars |
| `permission.asked` / `permission.replied` | permission flow | optional |
| `experimental.session.compacting` | before compaction summary | inject persistent project context |

Plugins can also register custom tools via `tool({...})` with Zod-style schemas (`tool.schema.*`) — plugin tools override built-ins on name collision. Logging: `client.app.log({ body: { service, level, message, extra } })`.

## Changelog (append-only after Day 1)

- 2026-09-23 — Day 1: routes, MCP tool names, env vars frozen per build plan §1. Day 1 additions: `POST /projects`, `DATABASE_URL` (engine), `SCAFFOLD_ENGINE_URL` (plugin). Implemented on Day 1: `/health`, `POST /projects`, `/context`, tasks CRUD. Deferred to Day 2: decisions, contracts, webhook. Day 3: `/reason`. Plugin hook API section appended from live docs verification.
- 2026-09-24 — Day 2: `POST /projects/:id/github-webhook` IMPLEMENTED (HMAC-SHA256 verified, push only; ping answered; fetches diffs via GitHub REST, parses deterministically via `diff_parser.py`, writes commits + api_contracts + events). Decisions + contracts GET/POST routes IMPLEMENTED (plain CRUD per frozen shapes). MCP server LIVE at `/mcp` — all 6 tools on real Postgres; each takes optional `project_id` (falls back to `SCAFFOLD_DEFAULT_PROJECT_ID`); list tools return object-shaped results. New env var: `SCAFFOLD_DEFAULT_PROJECT_ID` (engine, optional).
- 2026-09-25 — Day 4 (Person A, plugin hooks): the OpenCode plugin now speaks MCP to `/mcp` (no REST shortcut — `report_change` has no route). `tool.execute.before` calls `get_api_contract(route)` for routes found in write-tool arguments and pins them into the next request;`tool.execute.after` calls
`report_change(diff_summary, files_changed)` for `write`/`edit`/`apply_patch` (reports the engine refuses are parked and replayed oldest-first when it recovers); the always-on block from `get_project_context()` is injected via `experimental.chat.system.transform` (the fork's `tool.execute.before` cannot add prompt text), refreshed per user message, bounded at 2400 chars. NO contract changes: routes, MCP tool names/signatures and the env var list are untouched (no new env vars — the plugin omits `project_id` and the engine applies `SCAFFOLD_DEFAULT_PROJECT_ID`). Plugin-only checks: `cd opencode-plugin && node tests/verify_plugin.ts` (55 checks, fake engine), `npm run typecheck` (strict tsc against the vendored `@opencode-ai/plugin` types) and `python opencode-plugin/tests/mcp_sdk_interop.py` (17 checks against the real `mcp` SDK server with fixture tools — same wire format as `/mcp`, no Postgres needed). Against a live engine: `python opencode-plugin/tests/acceptance_live.py` prints the block an agent receives.
- 2026-09-24 — Day 3: `POST /projects/:id/reason` IMPLEMENTED (LiteLLM → Gemini `gemini/gemini-2.5-flash`, embeddings `gemini/gemini-embedding-001` @ 1536 dims; response shape unchanged). `commits.summary` now filled by the LLM (fail-open → null on provider outage; diff parsing stays deterministic). `GET /projects/:id/context` now returns real `recent_decisions` (newest 5, `{id, text, created_at}`) and `relevant_contracts` (newest 5, `{route, method}`) — shape unchanged, same data now feeds `get_project_context()` via MCP. New OPTIONAL env vars: `SCAFFOLD_LLM_MODEL`, `SCAFFOLD_EMBED_MODEL` (engine — override the Gemini defaults; no change to the frozen list). Retrieval: pgvector cosine search over new nullable `embedding` columns with deterministic keyword/recency fallback; `/reason` returns 503 without `SCAFFOLD_TEAM_LLM_KEY`, 502 on provider error.
- 2026-09-24 — Day 4 Part B: conflict detection + auto-GitHub-issue added (engine only, no route renames). `POST /projects/:id/contracts` and `POST /projects/:id/github-webhook` now run deterministic conflict detection (repo rule #3; new `engine/app/services/conflict_service.py`) against the project's registered contracts BEFORE the write: on a hit they write a `conflict_flagged` event, upsert a deduped `blockers` row, and best-effort file a GitHub issue (new optional env var `SCAFFOLD_GITHUB_REPO`="owner/repo", default `themdmohsin/Scaffold`; requires `GITHUB_TOKEN`; fail-open — issue failure never blocks ingestion). ADDITIVE response keys: POST /contracts may include `"conflicts": {events, blockers, issues}` when findings exist (201 status unchanged); webhook `processed[]` entries may include `"conflicts": <count>`. Detection rules: `conflicting_shape` (same method+route, schemas diverge — both sides must declare schemas), `method_divergence` (same normalized path, different method), `sibling_collision` (warning — same 2-segment feature prefix, different path). Pure re-registrations are NOT conflicts. No schema changes (uses existing `blockers` table + `conflict_flagged` event type).

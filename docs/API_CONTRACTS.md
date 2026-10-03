# API_CONTRACTS.md — Scaffold Engine Contract

> **FROZEN 2026-09-23 (Day 1).** This file is **append-only** after Day 1. Any change needs a one-line note to the team and a changelog entry at the bottom.

Base: the `engine/` FastAPI service (`VITE_ENGINE_URL` for clients, `SCAFFOLD_ENGINE_URL` for the plugin). All bodies are JSON. Errors use FastAPI's default shape: `{"detail": "..."}` with proper 4xx/5xx status.

## Routes (Day 1 freeze)

### `GET /health`
Liveness probe. `200 {"status": "ok"}` — no auth, no DB dependency.

### `GET /ready` *(Day 9 addition)*
Readiness probe for orchestrators (unauthenticated — a load balancer cannot carry a
credential). Checks the database and the migration manifest; `503` until both are good,
so a rolling deploy never routes traffic to an engine that cannot serve.
```jsonc
// 200 ready
{ "status": "ready", "checks": { "database": "ok", "migrations": "ok" }, "version": "0.3.0" }
// 503 not ready (any of)
{ "status": "not_ready", "checks": { "database": "unavailable", "migrations": "unavailable" }, ... }
{ "status": "not_ready", "checks": { "database": "ok", "migrations": "pending:2" }, ... }
```
`checks.migrations` is `ok` | `pending:<n>` | `checksum_warning` | `unavailable`;
`checks.database` is `ok` | `unavailable`. The body carries no configuration values and
no error detail (those go to the structured log). `/health` stays the pure liveness probe.

### Response headers & rate limiting *(Day 9 addition)*
Every response carries `X-Request-Id` (echoed from a safe incoming header, otherwise
generated) — quote it when reporting a problem. A deterministic in-process limiter
(sliding 60s window per `scope:client-ip:credential-fingerprint`) covers three scopes:
`auth` (`/auth/*`, `POST /projects/join`, `POST .../invite`), `reason`
(`POST /projects/:id/reason`), and `secret` (environment value endpoints).
Exact shape is unchanged: `429 {"detail": "rate limit exceeded for <scope> endpoints — retry in Ns"}`
with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` headers. Limits come from
`SCAFFOLD_RATE_LIMIT_*_PER_MINUTE`; `SCAFFOLD_RATE_LIMIT_ENABLED=0` disables. In-process =
per engine replica (see `docs/OPERATIONS.md` §5).

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
  "blockers": [ /* Day 5 additive: open blockers, newest first, max 10: {id, description, resolved, created_at, task_id} — task_id added Phase 2, null for contract-conflict blockers */ ],
  "recent_events": [ /* Day 5 additive: newest 8 events */ ],
  "generated_at": "ISO-8601",
  "task_counts": { "todo": 0, "in_progress": 0, "review": 0, "done": 0, "blocked": 0 }, // Phase 2 additive
  "open_conflicts": 0 // Phase 2 additive — unresolved blockers with no task_id (contract conflicts)
}
```
`404` if the project doesn't exist.

### `GET /projects/:id/tasks`
`200 [ TaskOut ]` — ordered by `created_at` ascending. `404` unknown project. `TaskOut` (Phase 2 additive fields marked):
```jsonc
{
  "id", "title", "status", "owner_id", "due_at", "created_at",           // frozen Day 1
  "description", "priority", "blocked", "created_by", "completed_at",    // Phase 2 additive
  "dependencies": [ { "id", "title", "status" } ],                       // Phase 2 additive — tasks this one depends on
  "blocked_by_dependencies": [ { "id", "title", "status" } ],            // Phase 2 additive — subset of the above not yet done
  "is_blocked": false                                                    // Phase 2 additive — blocked OR blocked_by_dependencies non-empty
}
```

### `POST /projects/:id/tasks`
```jsonc
// request
{
  "title": "required",
  "owner_id": "optional uuid",
  "due_at": "optional ISO-8601",
  "description": "optional",                 // Phase 2 additive
  "priority": "low|medium|high|urgent",      // Phase 2 additive — default 'medium'
  "created_by": "optional uuid",             // Phase 2 additive
  "dependencies": ["optional uuid", "..."]   // Phase 2 additive — creates task_dependencies rows
}
// response 201 — the full TaskOut object
```
`404` unknown project, `400` missing title, invalid `owner_id`/`created_by`/`priority`/`due_at`, or a `dependencies` entry that isn't a task on this project (or is the task itself). Writes an `events` row of type `task_created` (unchanged payload); additionally writes `task_assigned` when `owner_id` is set.

### `PATCH /projects/:id/tasks/:task_id`
```jsonc
// request — any of
{
  "status": "todo" | "in_progress" | "review" | "done",  // 'review' added Phase 2
  "owner_id": "optional uuid",
  "title": "optional",                 // Phase 2 additive — full edit
  "description": "optional",           // Phase 2 additive
  "priority": "low|medium|high|urgent",// Phase 2 additive
  "blocked": true | false,             // Phase 2 additive — manual block switch
  "blocker_reason": "optional"         // Phase 2 additive — only read when blocked:true; becomes the paired blocker's description
}
// response 200 — the full updated TaskOut object
```
`404` unknown project or task, `400` invalid status/priority value. Writes an `events` row of type `task_updated` (unchanged payload shape: `task_id` + the changed fields). Phase 2 additionally writes narrower events for the activity feed when the corresponding field changes: `task_status_changed` (`{task_id, title, from, to}`), `task_completed` (on transition to `done`), `task_assigned` (`{task_id, title, owner_id}`), `task_priority_changed` (`{task_id, title, from, to}`), `task_blocked` / `task_unblocked` (`{task_id, title, reason?}`). Setting `blocked: true` upserts an open `blockers` row (`task_id` set, deduped against an existing open one); setting `blocked: false` resolves that project's open blockers for the task.

### `POST /projects/:id/tasks/:task_id/dependencies` *(Phase 2 addition)*
```jsonc
// request
{ "depends_on_task_id": "required uuid" }
// response 201 — the full updated TaskOut object (dependencies/blocked_by_dependencies reflect the new edge)
```
`404` unknown project/task, `400` if `depends_on_task_id` is the task itself or not a task on this project. Idempotent (adding an existing edge is a no-op). Writes an `events` row of type `task_dependency_added`.

### `DELETE /projects/:id/tasks/:task_id/dependencies/:depends_on_task_id` *(Phase 2 addition)*
`200` — the full updated TaskOut object. Idempotent (removing a missing edge is a no-op). Writes an `events` row of type `task_dependency_removed`.

### `GET /projects/:id/users` *(Phase 2 addition)*
`200 [ { "id", "project_id", "name", "role" } ]` — ordered by `name` ascending. `404` unknown project. Read-only; the `users` table and its write paths (join flow) are unchanged. Powers the dashboard's assignee picker and "Active work" names.

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

## Authentication & authorization (Phase 6 — additive)

Every route and every MCP tool requires `Authorization: Bearer <credential>` — with exactly two exceptions: `GET /health` (liveness, no auth, no DB) and `POST /projects/:id/github-webhook` (authenticated by GitHub's HMAC-SHA256 signature; GitHub cannot carry a user credential, and a missing/bad signature is 403). Two credential kinds are accepted:

- **Supabase Auth JWT** (humans; `aud="authenticated"`, `iss=<SUPABASE_URL>/auth/v1`, requires `exp` + `sub`) — verified via the project JWKS (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`; ES256/RS256 by header `kid`, cached, refreshed on unknown kid with a cooldown) - or, for legacy HS256 projects only, `SUPABASE_JWT_SECRET`. iss/aud/exp/nbf are validated on both paths. The account is find-or-created in `accounts` from the token's `sub`; email/full_name come from the token claims. Configure email+password and GitHub OAuth in Supabase Auth. **The dashboard UI thread is separate; the API it needs is listed at the end of this section.**
- **Personal access token** (machines: OpenCode plugin / CLI / MCP) — `scaffold_` + 32 random bytes, SHA-256-hashed in `personal_access_tokens` (raw shown once, revocable, never stored), optionally scoped to ONE project.

Identity is NEVER read from a body or query. Legacy fields (`requesting_user_id`, `user_id`, the `name` on join, `granted_by`) are accepted-but-ignored where the old routes used them, so old clients don't hard-fail — they cannot change WHO the caller is.

Status codes: **401** no/invalid/revoked credential; **403** valid credential but not an active member of that project (or role too low); **503** `SUPABASE_JWT_SECRET` unset — the engine fails CLOSED rather than trusting anything.

Roles (`project_members.supabase_role`): `member` = read + tasks/decisions/contracts/agents + MCP reads + granted env values; `admin` = + member management + invite create/list/revoke; `owner` = + env variable/access mutations, ownership transfer. Exactly one active owner per project (enforced in `services/auth.py`).

### `GET /auth/me` *(Phase 6)*
`200` — the VERIFIED identity + memberships (the first call a dashboard makes after login):
```jsonc
{ "account_id": "...", "via": "jwt" | "pat", "email": "...", "full_name": "...",
  "auth_provider": "email" | "github", "token_scoped_project_id": null,
  "memberships": [ { "project_id": "...", "supabase_role": "owner", "joined_at": "..." } ] }
```

### `POST /auth/tokens` *(Phase 6)*
Mint a PAT for the CALLER. JWTs only — a PAT cannot mint further tokens (403).
```jsonc
// request
{ "name": "laptop", "project_id": "optional uuid — pin the token to ONE project", "scopes": [] }
// response 201 — THE ONLY RESPONSE THAT EVER CONTAINS THE RAW TOKEN
{ "id": "...", "token": "scaffold_...", "name": "laptop", "token_prefix": "scaffold_abc12…",
  "project_id": null, "created_at": "...",
  "warning": "Store this token now — it is shown once and cannot be recovered." }
```

### `GET /auth/tokens` *(Phase 6)*
`200 [ { id, name, token_prefix, project_id, created_at, last_used_at, revoked_at } ]` — the caller's own tokens, metadata only (no token material can ever appear here).

### `DELETE /auth/tokens/:token_id` *(Phase 6)*
`200 { "revoked": true, "id": "..." }` (+ `"already_revoked": true` on a re-revoke). Own tokens only; anyone else's id 404s. Revocation takes effect on the NEXT request (no cached access).

### `GET /projects` *(Phase 6)*
`200 { "projects": [ { "id", "name", "goal", "deadline", "created_at", "supabase_role", "joined_at" } ] }` — projects where the caller is an ACTIVE member, oldest first.

### `POST /projects` (changed Phase 6)
Same request body as Day 1 plus optional `requesting_user_id` (accepted, ignored). The CALLER becomes the project's owner: an owner `project_members` row, a roster `users` row, and `projects.owner_user_id` are created. `409` duplicate name, `401`/`503` per above.

### `POST /projects/join` (changed Phase 6)
Requires a JWT (`via="jwt"` — PATs get 403 "joining requires a signed-in Supabase session"). The `code` selects the PROJECT; the token selects the IDENTITY. Body `name`/`role` are accepted-but-ignored. `201` new join / `200` idempotent re-join (`"existing": true`); new joins add `supabase_role`. Invite checks: exact-row lookup (a revoked code can never be bypassed by minting a fresh one), revoked → 403, expired → 400, uses exhausted → 403. Each redemption writes an `invite_redemptions` row and increments `use_count`.

### Invite governance (Phase 6 — additive on the Day-5 invite/join pair)
- `POST /projects/:id/invite` — admin-gated; row-backed (reuses the newest live default invite, else creates one). Additive body knobs: `supabase_role` (default `member`), `max_uses`, `ttl_seconds`; response adds `invite_id`, `supabase_role`, `max_uses`, `use_count`, `revoked` to the Day-5 shape. Old `project_id.expiry.sig` links keep working: join mints a governed row for a legacy code on first post-auth use.
- `GET /projects/:id/invites` — admin-gated; every invite with `redemptions: [{ account_id, email, name, redeemed_at }]` ("who joined").
- `DELETE /projects/:id/invites/:invite_id` — admin-gated; soft-revoke (`revoked_at`).
- `POST /projects/:id/agents` — agents attach to the CALLER's account (`users.account_id`); a PAT request that registered an agent resolves AS that agent (own recommendations, own env grants).

### MCP (`/mcp`)
`Authorization: Bearer` is required on EVERY request, `initialize` included; unauthenticated requests get 401 before any tool code runs (ASGI gate in `app/main.py`). JWTs and PATs are both accepted; tool names/signatures and response shapes are unchanged.

### CORS
Allowed browser origins come from `SCAFFOLD_CORS_ORIGINS` (comma-separated). Empty = wildcard (local dev only). With explicit origins, credentials are allowed. Set it in any shared deployment.

### What the dashboard needs (API side — the UI is a separate thread)
1. Sign in with Supabase Auth (email/password + GitHub OAuth); send the session JWT as `Authorization: Bearer` to the engine on every call.
2. Use the Supabase anon key ONLY for Realtime/PostgREST reads — RLS scopes them to active members (`docs/SCHEMA.md` Phase 6).
3. After login: `GET /auth/me` → memberships + `supabase_role` drive which actions render.
4. Project picker: `GET /projects`; create: `POST /projects`; Team panel: `GET /projects/:id/members` + `GET /projects/:id/invites` (admin+).
5. Remove the "acting as" dropdown: identity is the session. `requesting_user_id`/`user_id` params are ignored by the engine now; token management is `GET/POST /auth/tokens` + `DELETE /auth/tokens/:id`.

## Team collaboration routes (Day 5 + Phase 3 — additive, not in the Day 1 freeze list)

### `POST /projects/:id/invite` *(Day 5)*
`201 {"invite_url", "code", "expires_at_epoch"}` — stateless `project_id.expiry.hmac` code signed with `GITHUB_WEBHOOK_SECRET`, 7-day TTL. No DB row; no invites table.

### `POST /projects/join` *(Day 5)*
```jsonc
// request
{ "code": "required", "name": "required", "role": "optional" }
// response 201 (new) / 200 (idempotent re-join, same name case-insensitive)
{ "user_id": "...", "project_id": "...", "name": "...", "role": null, "existing": false }
```
Creates the project's `users` row (writes `teammate_joined` event). This is Scaffold's invitation-acceptance step — see Phase 3 below for what a joined user becomes on the roster.

### `GET /projects/:id/members` *(Phase 3)*
Roster of every non-removed `users` row on the project, with a computed (not stored) activity status — see `docs/SCHEMA.md` Phase 3 entry and `engine/app/services/team.py`.
```jsonc
// response 200
[{
  "id": "...", "name": "...", "role": "backend",
  "kind": "developer",              // 'developer' | 'agent'
  "agent_provider": null, "agent_model": null,
  "membership_status": "active",
  "joined_at": "ISO-8601",
  "current_task": { "id": "...", "title": "...", "status": "in_progress" } | null,
  "activity_status": "ACTIVE",       // 'ACTIVE' | 'IDLE' | 'BLOCKED' | 'OFFLINE' — derived, not live presence
  "last_activity_at": "ISO-8601" | null
}]
```

### `PATCH /projects/:id/members/:member_id` *(Phase 3)*
`{"role"?, "kind"?, "requesting_user_id"?}` → 200 the updated member. Owner-gated once `projects.owner_user_id` is set (403 if `requesting_user_id` isn't the owner); open otherwise. Writes `member_role_changed`.

### `DELETE /projects/:id/members/:member_id` *(Phase 3)*
`?requesting_user_id=...` → 200 `{"id", "membership_status": "removed"}`. Soft-delete (row kept for historical task ownership); 400 if `member_id` is the current project owner. Writes `member_removed`.

### `POST /projects/:id/agents` *(Phase 3)*
```jsonc
// request
{ "name": "required", "provider": "optional", "model": "optional", "session_id": "optional" }
// response 201
{ "id", "name", "kind": "agent", "agent_provider", "agent_model", "agent_session_id", "is_new" }
```
Registers or upserts (by project + name) an AI agent as a first-class `users` row (`kind='agent'`). Provider/model are free text — no vendor is hardcoded into the data model. Writes `agent_registered` (first call) or `agent_session_updated` (subsequent calls).

### `POST /projects/:id/owner` *(Phase 3)*
```jsonc
// request
{ "user_id": "required", "requesting_user_id": "optional" }
// response 200
{ "project_id", "owner_user_id" }
```
Bootstraps ownership (open, first call — no owner yet) or transfers it (`requesting_user_id` must equal the current owner, else 403). Writes `project_owner_set`.

Task assignment to a member or an agent reuses the existing, frozen `tasks.owner_id` field and `PATCH /projects/:id/tasks/:task_id` route unchanged — there is no second task/assignment system; `kind` on the assigned `users` row is how the dashboard tells a developer from an agent.

Authorization note: this repo has no authentication system. `requesting_user_id` is a placeholder authorization hook (compared server-side to `projects.owner_user_id`), not a verified identity — every other route in this API has the same trust model today. It becomes a real session identity once auth (e.g. Supabase Auth) is wired in.

> **Phase 6 note (2026-10-03):** this placeholder model is now SUPERSEDED — see "Authentication & authorization" above. Every route here requires `Authorization: Bearer`; `requesting_user_id` is accepted-but-ignored; `PATCH/DELETE /members` are admin-gated and `POST /owner` is owner-gated with a removed-member guard. The paragraph above is kept as the historical Day-5..Phase-3 contract.

## Intelligent coordination routes (Phase 4 — additive)

Every endpoint here is deterministic engine state (repo rule #3) — no LLM call happens unless `explain=true` is passed, and the LLM only phrases facts the deterministic layer already decided (fail-open: without a key the endpoints work unchanged, minus the one-line `explanation`).

Task coordination states: `READY` (todo + not blocked + all dependencies done), `BLOCKED` (open blockers row / manual switch), `WAITING_ON_DEPENDENCY` (todo, an upstream is not done), `IN_PROGRESS`, `REVIEW`, `DONE`.

### `GET /projects/:id/tasks/ready` *(Phase 4)*
Query: `?user_id=<member uuid>` (optional — personalizes the ranking), `?explain=true` (optional LLM one-liner per row).
```jsonc
// response 200
{
  "ready_tasks": [ { "id", "title", "status", "priority", "owner_id", "owner_name", "owner_kind", "score", "score_factors": {"priority":40, "assignment":25, ...}, "reasons": ["status is todo", "no blocker is active", ...], "downstream_open": 2, "dependencies": [...], "ready": true } ],
  "count": 3,
  "ready_task_ids": ["..."],
  "unassigned_ready_count": 1,
  "project_task_states": { "READY": 2, "BLOCKED": 1, "WAITING_ON_DEPENDENCY": 1, "IN_PROGRESS": 1, "REVIEW": 0, "DONE": 2 },
  "generated_at": "ISO-8601"
}
```
Ordered by the explainable ranker (score desc, then oldest first, then id). `404` unknown project, `400` if `user_id` is not a member of THIS project.

### `GET /projects/:id/recommendations?user_id=<member uuid>` *(Phase 4)*
"What should I work on?" for one member (developer or agent — both are `users` rows; the agent's identity is its own roster entry, no provider hardcoded). Deterministic pipeline: drop done → drop this member's recently rejected → never recommend others' active work → own ready work → continue own in_progress → the ready dependency that unblocks own blocked work → claimable unassigned ready work → honest note.
```jsonc
// response 200
{
  "project_id": "...",
  "user": { "id", "name", "kind" },
  "recommendation": { "task": {"id","title","status","priority","owner_id","due_at"}, "state": "READY", "score": 95, "score_factors": {...}, "reasons": [...], "downstream_open": 0, "explanation": "LLM one-liner, only with explain=true" } | null,
  "alternates": [ /* up to 3, same shape */ ],
  "current_work": [...],   // this member's in_progress tasks with computed state
  "blocked_work": [...],   // blocked/waiting tasks with waiting_on reasons
  "claimable_count": 2,
  "note": "honest no-work note, only when nothing is actionable",
  "conflict_awareness": { "cross_owner_dependencies": [...], "contract_collisions": [...], "open_conflicts": [...] },
  "generated_at": "ISO-8601"
}
```

### `GET /projects/:id/recommendations/next` *(Phase 4)*
Same engine as `/recommendations`, trimmed to `{project_id, user, recommendation, alternates, note, generated_at}` — the single best next move for the requester.

### `POST /projects/:id/recommendations/next` *(Phase 4)*
```jsonc
// request  { "user_id": "optional member uuid" }
// response 200
{
  "project_id": "...", "user": null,
  "action": {
    "kind": "resolve_conflict" | "review_task" | "unblock_task" | "start_task" | "all_clear",
    "title": "Resolve the open contract conflict: ...",
    "task": { ... } | null,
    "conflict": { "id", "description", "created_at" } | null,
    "reasons": ["...", "..."]
  },
  "task_states": { ... }, "open_conflicts": 1, "generated_at": "..."
}
```
Project-level "what should happen next?" — deterministic precedence: resolve conflict > review > unblock (finish the dependency that unblocks waiting work) > start (top ready, unassigned preferred) > all clear. Every kind carries explicit `reasons`.

### `GET /projects/:id/coordination` *(Phase 4)*
One bounded payload for the dashboard's Next Actions panel (deterministic only — safe on every dashboard load):
```jsonc
{
  "project_id": "...",
  "task_states": { "READY": 0, "BLOCKED": 0, "WAITING_ON_DEPENDENCY": 0, "IN_PROGRESS": 0, "REVIEW": 0, "DONE": 0 },
  "ready_to_start": [ /* ranked ready rows with reasons */ ],
  "blocked": [ { "id", "title", "state", "priority", "owner_id", "owner_name", "waiting_on": [{"id","title","status"}], "manual_blocker" } ],
  "needs_review": [ { "id", "title", "owner_id", "owner_name", "created_at" } ],
  "conflicts": {
    "open_contract_conflicts": [ /* unresolved blockers with task_id null (Day 4 detector) */ ],
    "task_overlaps": [ { "task_a", "task_b", "shared_terms", "similarity" } ],  // conservative title+description token overlap — surfaces suspicion, mutates nothing
    "contract_collisions": [ { "route", "method", "tasks": [...] } ],          // two OPEN tasks registered the SAME method+route (grounded in api_contracts.created_by_task_id)
    "cross_owner_dependencies": [ { "waiting_task", "waiting_owner", "blocking_task", "blocking_owner" } ]
  },
  "recommended_next_step": { /* same shape as POST next.action */ },
  "who_is_doing_what": [ { "user_id", "name", "kind", "open_tasks": [...] } ],
  "generated_at": "..."
}
```

### `POST /projects/:id/tasks/:task_id/accept-recommendation` *(Phase 4 — human override)*
```jsonc
// request  { "user_id": "required member uuid", "note": "optional" }
// response 201 (assigned) / 200 (idempotent re-accept)
{ "accepted": true, "task_id": "...", "task": { ...full TaskOut... }, "owner_id": "...", "idempotent": false }
```
Human override — ACCEPT. Claims the task for the deciding member by assigning the existing frozen `tasks.owner_id` (no second assignment system) and writes `recommendation_accepted` (+ `task_assigned` when ownership changed). Scaffold NEVER takes ownership of a task on its own — only an explicit POST does. `400` if the task is done; `404` unknown project/task; `400` if `user_id` is not a member.

### `POST /projects/:id/tasks/:task_id/reject-recommendation` *(Phase 4 — human override)*
```jsonc
// request  { "user_id": "required member uuid", "note": "optional" }
// response 200 { "rejected": true, "task_id": "...", "user_id": "..." }
```
Human override — REJECT. Writes a `recommendation_rejected` event; the deterministic recommender excludes this task for this member for 7 days (`services/coordination.py`), then it becomes eligible again. Nothing else changes — no status flip, no assignment; fully reversible by accepting.

### New Phase 4 MCP tools (additive — the six frozen tools are untouched)
```
get_ready_tasks(project_id?)                          → { tasks: [ ranked ready rows with reasons ], count }
get_recommended_task(user_id? = null, project_id?)    → { recommendation, alternates, note } — per-member
                                                        "what should I work on?", or the project-level
                                                        next action when user_id is omitted
```
Both are deterministic (no LLM) and reuse the same pure service as the HTTP routes.

### New Phase 5 MCP tools (additive — the eight frozen tools are untouched)
```
get_project_environment(project_id?)                    → { variables: [{ key, description, required,
                                                            is_secret, configured, ... }], summary }
                                                          metadata + configured BOOLEANS only — no value field exists
get_environment_template(project_id?)                   → { filename, content, variables } — KEY= lines, never values
request_environment_value(key, user_id? = null, project_id?)
                                                          → { authorized: true, key, value } for a GRANTED agent/member
                                                          → { authorized: false, reason } for everyone else (structured
                                                            denial, NOT an error) — the runtime carrier for Feature 4/10;
                                                          user_id omitted resolves ONLY when exactly one active agent exists
```
Deterministic (no LLM); grant/membership checks identical to the HTTP routes.
Secret VALUES reach only the authorized agent's runtime through this one tool —
never through get_project_context(), never through any other tool.

## Secure environment routes (Phase 5 — additive, `feature/secure-environment`)

Three strictly separated layers, matching the DB layers in `docs/SCHEMA.md`:
metadata (status — NO values ever), permissions (grants), and values (only the
two retrieval routes below may carry a value, and only after membership +
per-variable grant checks). Owner-gated mutations use the same placeholder
`requesting_user_id` model as Phase 3 (compared to `projects.owner_user_id`
once an owner exists). Membership = an active `users` row on THIS project.
```
GET   /projects/:id/environment
// → 200 { project_id, project_name, variables: [{ id, key, description, required,
//        is_secret, configured, status, display_status, created_by, created_at, updated_at }],
//        summary: { total, configured, required_missing, optional_missing } }
// NO field can carry a value — pinned by tests/test_phase5.py. status ∈
// configured | required_missing | optional_missing.
POST  /projects/:id/environment/variables
// request  { key, description?, required?, is_secret?, created_by?, value? }
// → 201 variable object (same shape as above minus status); `value` is consumed
//   by the secret store in-request, NEVER echoed. 400 bad key shape, 409 dup, 503 store down.
PATCH /projects/:id/environment/variables/:variable_id
// request  { description?, required?, is_secret?, value?, value_changed?, requesting_user_id? }
// value_changed=true + value = SET (new) or ROTATE (existing) — metadata and grants
// untouched. Response is the variable object, never the value.
DELETE /projects/:id/environment/variables/:variable_id?requesting_user_id=
// → { removed: true, id, key } — ciphertext removed with the metadata (no orphans).
POST  /projects/:id/environment/access
// request  { environment_variable_id, user_id, granted_by?, requesting_user_id? }
// → 200 grant object { id, key, user_id, user_name, granted_by, created_at }.
// 403 non-owner, 400 non-member target. Idempotent (re-grant returns the same row).
GET   /projects/:id/environment/access?environment_variable_id=
// → [ grant objects ] — who may retrieve what (metadata only).
DELETE /projects/:id/environment/access/:grant_id?requesting_user_id=
// → { revoked: true, id } — takes effect on the NEXT request (no cached access).
POST  /projects/:id/environment/request
// request  { key, user_id }   ← the agent/developer secret request (Feature 4)
// → 200 { key, value } ONLY for granted active members — this and /pull are the
//   only value carriers in the entire API. 403 no grant / not a member (grant check
//   precedes the configured check, so configuration status never leaks),
//   404 unknown key or defined-but-unconfigured, 503 store down.
POST  /projects/:id/environment/pull
// request  { user_id }   ← `scaffold env pull` transport (Feature 5)
// → 200 { project_id, project_name, values: [{ key, value, description, is_secret }],
//        granted_count, total_count, file_body, filename: ".env.scaffold", warnings }
// values contains ONLY the caller's granted variables (no grant → omitted, never
// an error). file_body is a ready-to-write .env.scaffold with a never-commit header.
GET   /projects/:id/environment/template
// → 200 { filename: ".env.example", content, variables: [{ key, description }], count }
// content is KEY= lines ONLY — values are impossible here by construction.
GET   /projects/:id/environment/audit?limit=50
// → 200 { project_id, events: [{ id, type, payload, created_at }] } — env_* event
// types only, newest first. Payloads record WHO/WHAT/OUTCOME, never a value.
```
Frozen-contract guarantees: `GET /projects/:id/context` gains NOTHING env-related
(so `get_project_context()` and the AI prompt pipeline can never see a value);
all Phase 1-4 routes are untouched.

## MCP server (Day 2 — LIVE at `/mcp`, streamable HTTP)
The OpenCode plugin calls these verbatim — do not rename. All tools hit real Postgres; deterministic logic only (repo rule #3). Every tool takes an optional `project_id`; when omitted the server uses `SCAFFOLD_DEFAULT_PROJECT_ID` (engine .env) — the single-project demo convention. **Phase 6: every request requires `Authorization: Bearer <JWT or PAT>` and the caller must be an active member of the target project; the resolved identity is used for agent recommendations, agent registration, and env grants.**
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
SCAFFOLD_SECRET_KEYRING=        # Phase 5 OPTIONAL — name of the active scaffold_secrets.keyring row;
                                # unset = newest row (`default`). The KEY ITSELF is generated inside
                                # Postgres and never lives in an env file.
SUPABASE_JWT_SECRET=            # Phase 6 REQUIRED — Supabase Dashboard → Settings → API → JWT Secret.
                                # Verifies Supabase Auth JWTs; unset = every authenticated route 503 (fail closed).
SCAFFOLD_CORS_ORIGINS=          # Phase 6 — comma-separated allowed browser origins (e.g. http://localhost:5173).
                                # Empty = wildcard (DEV ONLY); set explicitly in any shared deployment.
SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS= # Phase 6 DEV/SEED ONLY — comma-separated Supabase user ids auto-promoted
                                # to OWNER on any project they touch (pre-auth project bootstrap). Leave EMPTY in prod.
SCAFFOLD_ENV=                   # Day 9 — "development" (default) or "production". Production makes startup
                                # config validation FATAL with actionable messages.
SCAFFOLD_LOG_FORMAT=            # Day 9 — json (default, one object per line) or text.
SCAFFOLD_LOG_LEVEL=             # Day 9 — default INFO.
SCAFFOLD_RATE_LIMIT_ENABLED=    # Day 9 — default 1; 0 disables the limiter (load tests).
SCAFFOLD_RATE_LIMIT_AUTH_PER_MINUTE=    # Day 9 — default 120.
SCAFFOLD_RATE_LIMIT_REASON_PER_MINUTE=  # Day 9 — default 30 (the paid LLM call).
SCAFFOLD_RATE_LIMIT_SECRET_PER_MINUTE=  # Day 9 — default 240.
SCAFFOLD_TRUST_PROXY=           # Day 9 — 1 behind Fly/Railway edge so rate-limit keys and access
                                # logs see the real client IP; 0 (default) when directly exposed.

# dashboard/.env
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_ENGINE_URL=

# opencode-plugin/.env (Day 1 addition — new component)
SCAFFOLD_ENGINE_URL=            # where the plugin's hooks reach the engine
SCAFFOLD_TOKEN=                 # Phase 6 — personal access token (`scaffold_…` from POST /auth/tokens).
                                # Sent as Authorization: Bearer on every MCP call; required by the engine.
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

- 2026-10-03 — Day 9 (deployment hardening, `day9-bf-deploy-hardening`): ONE shared engine for the team — container + deploy path + operational safety. New additive route `GET /ready` (readiness probe: DB + migration manifest; 503 until both are good — orchestrators gate traffic on it; `/health` unchanged as pure liveness). Every response now carries `X-Request-Id` (safe incoming header echoed, otherwise generated) and access logs are structured JSON by default with secret redaction (PATs, Bearer, JWTs, `password=`-style pairs, DSN passwords → `***`; bodies/headers never logged; `uvicorn.access` disabled in favor of the engine's own line). Basic deterministic in-process rate limiting (sliding 60s window, per scope:client-ip:credential-fingerprint) on `auth` / `reason` / `secret` scopes → `429` + `Retry-After` + `X-RateLimit-Limit`/`X-RateLimit-Remaining`; limits via `SCAFFOLD_RATE_LIMIT_*_PER_MINUTE`, disable via `SCAFFOLD_RATE_LIMIT_ENABLED=0`. Central config validation (`validate_settings()`): development boots with loud warnings, `SCAFFOLD_ENV=production` refuses to start on missing `DATABASE_URL`/`SUPABASE_JWT_SECRET`/CORS origins or an armed `SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS`. Ordered, idempotent migration runner (`app/db/migrations.py` + `schema_migrations` table) with CLI `python -m app.scripts.migrate [--status|--check|--strict|--retry-skipped]` and from-zero `python -m app.scripts.bootstrap_db [--lenient|--status]`; startup applies pending migrations fail-open, `/ready` reports pending. No route renamed, no frozen shape changed (all additive). New Day-9 env vars: `SCAFFOLD_ENV`, `SCAFFOLD_LOG_FORMAT`, `SCAFFOLD_LOG_LEVEL`, `SCAFFOLD_RATE_LIMIT_ENABLED`, `SCAFFOLD_RATE_LIMIT_{AUTH,REASON,SECRET}_PER_MINUTE`, `SCAFFOLD_TRUST_PROXY` (all optional, defaults preserve dev behavior). Fixes a real invite bug found during from-zero verification: two invites for the same project created in the same second with the same TTL produced the SAME code (`project_id.expiry.signature` is content-defined) and the second insert 500'd on `invites_code_key` — the route now allocates a unique expiry until the code is free (and `POST .../invite` gained no new failure mode). Also removed the stray committed `webhook-test.txt`. Verified from zero on a throwaway Postgres 17 + pgvector 0.8.1 rig (`bootstrap_db --lenient`: all 6 migrations applied, only Supabase-only RLS/role statements skipped by design; second run: `migrations already applied`): full engine sweep green — test_day2 31, test_day3 42, test_day4b 25, test_day5 76, test_phase2 46, test_phase3 41, test_phase4 125, test_phase5 91, test_auth 116 (+1 legitimate Supabase-only RLS skip), new test_deploy 84/84. Docker image build could NOT be verified on the dev machine (Docker Desktop cannot start: no WSL/hypervisor — the rig substituted for container E2E; `docker compose config` validates clean). Docs: `docs/DEPLOYMENT.md` (Fly.io recommended + Railway notes, env table, custom domain, webhook setup) and `docs/OPERATIONS.md` (backup/restore, secret-key rotation, incident runbook).
- 2026-10-03 — Phase 6 (real authentication, `day8-bf-real-auth`): the placeholder identity model is replaced. Every route + MCP tool now requires `Authorization: Bearer` (exceptions: `GET /health`, and the GitHub webhook, which authenticates via GitHub's HMAC signature) — a Supabase Auth JWT (humans) or a `scaffold_…` personal access token (plugin/CLI/MCP, SHA-256-hashed in `personal_access_tokens`, shown once, revocable, optionally project-scoped). Identity is resolved server-side and never from a body; the legacy `requesting_user_id`/`user_id`/`name`-on-join/`granted_by` fields are accepted-but-ignored everywhere they existed. New routes (full shapes in the new "Authentication & authorization" section): `GET /auth/me`, `POST /auth/tokens` (raw token exactly once), `GET /auth/tokens`, `DELETE /auth/tokens/:token_id`, `GET /projects` (my projects). `POST /projects` now makes the CALLER the owner; `POST /projects/join` requires a JWT (`via="jwt"`; PATs 403) and binds the VERIFIED principal; `POST /projects/:id/invite` is now row-backed and admin-gated (additive knobs `supabase_role`/`max_uses`/`ttl_seconds`; Day-5 response keys preserved) with new admin-gated `GET /projects/:id/invites` (incl. per-invite redemption trail) and `DELETE /projects/:id/invites/:invite_id`. Role model: member < admin < owner (`project_members.supabase_role`), enforced on routes AND MCP; unauthenticated → 401, non-member → 403, missing `SUPABASE_JWT_SECRET` → 503 fail-closed. `/mcp` is Bearer-gated at the ASGI layer (401 before tool code). Supabase RLS (SELECT-only, active members) covers every public table for the anon key + Realtime; engine uses the service key. CORS is now `SCAFFOLD_CORS_ORIGINS`. New env vars: `SUPABASE_JWT_SECRET`, `SCAFFOLD_CORS_ORIGINS`, `SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS` (engine, DEV/SEED only), `SCAFFOLD_TOKEN` (plugin). New tables/columns/RLS: `docs/SCHEMA.md` Phase 6. Verified: new `engine/tests/test_auth.py` 133/133 (credential primitives, 401/403 sweeps, role gates, PAT lifecycle, invite lifecycle, cross-project isolation, secret non-disclosure, live RLS enforcement as the `anon` role); full engine sweep green — test_day2 31, test_day3 42, test_day4b 25, test_day5 76, test_phase2 46, test_phase3 41, test_phase4 125, test_phase5 91; plugin: verify_plugin.ts 63/63 (every MCP call carries the PAT), typecheck clean, mcp_sdk_interop 17/17, acceptance_live --self-test 4/4 (live leg now refuses to run without `SCAFFOLD_TOKEN`).
- 2026-10-01 — Phase 5 (secure environment & context, `feature/secure-environment`): the SECURE-CONFIGURATION layer on top of COORDINATE. New additive routes (full shapes in the new "Secure environment routes" section): `GET /projects/:id/environment` (status + summary, value-free by construction), `POST/PATCH/DELETE /projects/:id/environment/variables[/:variable_id]` (metadata + set/rotate value), `POST/GET/DELETE /projects/:id/environment/access[/:grant_id]` (per-variable grants), `POST /projects/:id/environment/request` (the agent/developer secret request — the only single-value carrier, after membership + grant checks, with audit events), `POST /projects/:id/environment/pull` (granted-only `.env.scaffold` generation — the `scaffold env pull` transport), `GET /projects/:id/environment/template` (`.env.example` KEY= lines, never values), `GET /projects/:id/environment/audit` (value-free access trail). `GET /projects/:id/context` intentionally gains NO env keys — secrets can never enter embeddings, prompts, or `get_project_context()`. Three additive MCP tools: `get_project_environment`, `get_environment_template`, `request_environment_value` (authorized-only value; structured denial). New event types (all value-free): `env_variable_defined`, `env_value_set`, `env_access_granted`, `env_access_revoked`, `env_secret_requested`, `env_secret_access_denied`, `env_secret_retrieved`, `env_secret_rotated`, `env_variable_removed`, `env_variable_updated`. New tables + `scaffold_secrets` schema: see `docs/SCHEMA.md`. One new OPTIONAL env var: `SCAFFOLD_SECRET_KEYRING` (engine — pins the active keyring row name; the key material itself is generated inside Postgres, never in an env file). Plugin UNCHANGED by design — values flow MCP → agent runtime directly, never through the plugin or the dashboard. Verified: `python -m tests.test_phase5` 89/89 (pure units + DB-backed route legs incl. the non-disclosure sweeps over events/context/tasks/members/MCP and MCP over the real wire); full regression green — test_day2 31, test_day3 35, test_day4b 25, test_day5 74, test_phase2 43, test_phase3 40, test_phase4 125 (373 passed, 0 failed); `dashboard && npm run build` clean. Dashboard: new Environment panel (`components/Environment.tsx`) — configuration status (Configured / Required · Missing / Optional · Not configured), define/rotate/remove, grant/revoke per variable, template viewer; acting-as selector; there is NO code path that displays a stored value.
- 2026-09-30 — Phase 4 (intelligent coordination, `feature/intelligent-coordination`): the COORDINATE layer learns to answer "what should I work on?" and "what should happen next?" — deterministically first, AI only to explain. NO schema changes (pure computation over the existing tables; no migration file, nothing new stored — recommendation decisions are recorded as events only). New routes (all additive, see the "Intelligent coordination routes" section): `GET /projects/:id/tasks/ready`, `GET /projects/:id/recommendations`, `GET /projects/:id/recommendations/next`, `POST /projects/:id/recommendations/next` (project-level next action), `GET /projects/:id/coordination` (dashboard panel payload), and the human-override pair `POST /projects/:id/tasks/:task_id/accept-recommendation` / `POST .../reject-recommendation`. New `engine/app/services/coordination.py` (pure deterministic state classification READY/BLOCKED/WAITING_ON_DEPENDENCY/IN_PROGRESS/REVIEW/DONE, explainable scoring — priority/assignment/downstream-impact/deadline/age, every factor echoed as reasons — recommendation pipeline, project next action, overlap detection, cross-owner dependency awareness) + `coordination_data.py` (bounded plain-row loaders). Task states of record remain the existing `tasks.status`/`blocked`/`task_dependencies`/`blockers` columns; nothing is duplicated. Two additive MCP tools: `get_ready_tasks`, `get_recommended_task` (frozen six untouched). Two new event types: `recommendation_accepted`, `recommendation_rejected` (the only persistence — the human decision trail). LLM usage: one new fail-open helper `reasoning.explain_recommendation` (phrases already-decided facts into one sentence; only called with `explain=true`; reasoning.py remains the only LLM file). Human override: accept claims the task via the frozen `tasks.owner_id` assignment path (never automatic); reject suppresses the task for that member for 7 days (window constant in coordination.py). No new env vars. Verified: `python -m tests.test_phase4` 125/125 (pure units + DB-backed route legs incl. MCP over the wire); full regression re-run green — test_day2 31, test_day3 35, test_day4b 25, test_day5 74, test_phase2 43, test_phase3 40 (248 passed, 0 failed); `dashboard && npm run build` clean. Dashboard: new Next Actions panel (`components/NextActions.tsx`) — recommended next step with Accept/Not-now override, READY TO START / BLOCKED / NEEDS REVIEW / CONFLICTS columns (incl. "Potential overlap detected" rows), who-is-doing-what footer; loads via one deterministic GET (no LLM on dashboard load), degrades silently against older engines.
- 2026-09-30 — Phase 3 (team collaboration, `feature/team-collaboration`): new additive routes `GET /projects/:id/members`, `PATCH /projects/:id/members/:member_id`, `DELETE /projects/:id/members/:member_id`, `POST /projects/:id/agents`, `POST /projects/:id/owner` — full shapes in the new "Team collaboration routes" section above. AI agents are first-class `users` rows (`kind='agent'`, free-text `agent_provider`/`agent_model` — no vendor hardcoded). Task assignment reuses the existing frozen `tasks.owner_id` + `PATCH /projects/:id/tasks/:task_id` — no second task/assignment system. Activity status (`ACTIVE`/`IDLE`/`BLOCKED`/`OFFLINE`) is computed deterministically in `engine/app/services/team.py` from existing tasks/decisions/events — never an LLM call, never faked live presence. `GET /projects/:id/context` gains one ADDITIVE key on `project`: `owner_user_id` (null until `POST /owner` is called). Owner-gated mutations use a placeholder `requesting_user_id` field compared to `projects.owner_user_id` — not real auth (none exists yet anywhere in this API); becomes a real session identity once auth is wired in. No changes to Day 5 invite/join (still stateless HMAC codes, no invites table). New indexes + `users` columns (`kind`, `agent_provider`, `agent_model`, `agent_session_id`, `membership_status`, `joined_at`) and `projects.owner_user_id` — see `docs/SCHEMA.md`. No new env vars. Verified: `python -m tests.test_phase3` 40/40 (offline pure units + DB-backed route legs); full regression re-run clean — `test_day2` 31/31, `test_day3` 35/35, `test_day4b` 25/25, `test_day5` 74/74 (205 total, 0 failed); `dashboard && npm run build` clean (tsc + vite). New dashboard Team panel (`components/Team.tsx`) is additive/isolated — roster table, invite/join/agent-registration mini-forms, owner/role/remove actions; `App.tsx` gained one new fetch (`fetchMembers`, degrades to an empty roster against older engines) and one new panel mount.
- 2026-09-29 — Phase 2 (Project Control Center, Developer A, branch `feature/project-control-center`): the task board + coordination layer. `GET/POST /projects/:id/tasks` and `PATCH /projects/:id/tasks/:task_id` gain ADDITIVE fields only (`description`, `priority`, `blocked`, `created_by`, `completed_at`, `dependencies`, `blocked_by_dependencies`, `is_blocked`); every Day 1 field on those routes is byte-identical, and `task_created`/`task_updated` events keep their frozen payload shape. `status` CHECK widened to add `'review'` (`todo|in_progress|review|done`). New routes: `POST`/`DELETE /projects/:id/tasks/:task_id/dependencies[/:depends_on_task_id]` (task_dependencies CRUD, idempotent) and `GET /projects/:id/users` (read-only roster for the assignee picker). `GET /projects/:id/context` gains two ADDITIVE keys: `task_counts` (todo/in_progress/review/done/blocked) and `open_conflicts` (unresolved blockers with no `task_id` — i.e. contract-shape conflicts, distinct from task-level blockers). New event types for the activity feed (all additional to, never replacing, the frozen `task_created`/`task_updated`): `task_status_changed`, `task_completed`, `task_assigned`, `task_priority_changed`, `task_blocked`, `task_unblocked`, `task_dependency_added`, `task_dependency_removed`. No env var changes. No existing route, MCP tool, or event type removed or renamed.
- 2026-09-26 — Day 5 round 6 (full-repo audit): `POST /projects/:id/github-webhook` now ingests ONLY pushes to the default branch (`refs/heads/main`/`master`); feature-branch pushes are acknowledged with `{ok, processed: [], skipped: {branch, reason}}` so WIP routes never become contracts or fire false conflicts (payloads without `ref` — older fixtures — are treated as main, backwards compatible). Removed the stray undeclared `GET /api/test/scaffold-v2` route (was never in the frozen contract list). No other route/shape changes.
- 2026-09-26 — Day 5 (Person A, task assignment + invites): `/reason` may now return assignments. The engine computes a deterministic TEAM ROSTER (open-task count per user via SQL GROUP BY, hours-until-deadline from the project deadline — never LLM-guessed) into the `/reason` context, and every suggested task is re-validated AFTER the LLM: owner_ids not on the roster are cleared, past/unparseable due_at cleared, due_at beyond the project deadline clamped. Response shape unchanged (`{answer, suggested_tasks:[{title, owner_id, due_at}]}`) plus one ADDITIVE key `assignment_notes` (present only when something was corrected). New routes: `POST /projects/{id}/invite` → `{invite_url, code, expires_at_epoch}` (stateless `project.expiry.hmac` code signed with GITHUB_WEBHOOK_SECRET, 7-day TTL) and `POST /projects/join` `{code, name, role?}` → 201 `{user_id, project_id, name, role}` (creates the users row + `teammate_joined` event) — the first API that creates users. Join is IDEMPOTENT per name: re-joining with the same name (case-insensitive, whitespace-collapsed) returns 200 `{..., "existing": true}` with the SAME user_id instead of planting a duplicate row; blank names → 400; `role` is whitespace-collapsed at the boundary (a newline in a name OR role would forge a line inside the LLM TEAM ROSTER prompt). No DB schema changes. No new env vars (invites reuse GITHUB_WEBHOOK_SECRET; 503 if unset). `GET /projects/:id/context` gains two ADDITIVE keys (all pre-existing keys byte-identical): `blockers` — open blockers, newest first, ≤10, `{id, description, resolved, created_at}` — and `recent_events` — newest 8, `{id, type, payload, created_at}` — so the conflict moment (event + blocker) is visible through the endpoint the dashboard and MCP `get_project_context()` already poll, with no new route. Hardware-verified (2026-09-26) against a disposable local Postgres+pgvector rig (`scaffold-day5-pg`, port 5433): `python -m tests.test_day5` 74/74 checks — pure units PLUS the DB-backed route legs (that run caught and fixed a real API regression: new joins had silently started returning 200 instead of the frozen 201). `test_day2` 30/30 and `test_day4b` 25/25 re-verified on the same rig; `/reason` without `SCAFFOLD_TEAM_LLM_KEY` confirmed to degrade 503 fail-open. The live-LLM assignment path has now also run for real (`python -m app.scripts.live_reason_check`: project → invite → join → roster → Gemini → suggestions with roster-valid owner_ids; that run exposed Gemini 3 thinking tokens exhausting `max_tokens=800` and truncating the JSON mid-string — raised to 2000, pinned by a `test_day5` source check).
- 2026-09-23 — Day 1: routes, MCP tool names, env vars frozen per build plan §1. Day 1 additions: `POST /projects`, `DATABASE_URL` (engine), `SCAFFOLD_ENGINE_URL` (plugin). Implemented on Day 1: `/health`, `POST /projects`, `/context`, tasks CRUD. Deferred to Day 2: decisions, contracts, webhook. Day 3: `/reason`. Plugin hook API section appended from live docs verification.
- 2026-09-24 — Day 2: `POST /projects/:id/github-webhook` IMPLEMENTED (HMAC-SHA256 verified, push only; ping answered; fetches diffs via GitHub REST, parses deterministically via `diff_parser.py`, writes commits + api_contracts + events). Decisions + contracts GET/POST routes IMPLEMENTED (plain CRUD per frozen shapes). MCP server LIVE at `/mcp` — all 6 tools on real Postgres; each takes optional `project_id` (falls back to `SCAFFOLD_DEFAULT_PROJECT_ID`); list tools return object-shaped results. New env var: `SCAFFOLD_DEFAULT_PROJECT_ID` (engine, optional).
- 2026-09-25 — Day 4 (Person A, plugin hooks): the OpenCode plugin now speaks MCP to `/mcp` (no REST shortcut — `report_change` has no route). `tool.execute.before` calls `get_api_contract(route)` for routes found in write-tool arguments and pins them into the next request;`tool.execute.after` calls
`report_change(diff_summary, files_changed)` for `write`/`edit`/`apply_patch` (reports the engine refuses are parked and replayed oldest-first when it recovers); the always-on block from `get_project_context()` is injected via `experimental.chat.system.transform` (the fork's `tool.execute.before` cannot add prompt text), refreshed per user message, bounded at 2400 chars. NO contract changes: routes, MCP tool names/signatures and the env var list are untouched (no new env vars — the plugin omits `project_id` and the engine applies `SCAFFOLD_DEFAULT_PROJECT_ID`). Plugin-only checks: `cd opencode-plugin && node tests/verify_plugin.ts` (55 checks, fake engine), `npm run typecheck` (strict tsc against the vendored `@opencode-ai/plugin` types) and `python opencode-plugin/tests/mcp_sdk_interop.py` (17 checks against the real `mcp` SDK server with fixture tools — same wire format as `/mcp`, no Postgres needed). Against a live engine: `python opencode-plugin/tests/acceptance_live.py` prints the block an agent receives.
- 2026-09-24 — Day 3: `POST /projects/:id/reason` IMPLEMENTED (LiteLLM → Gemini `gemini/gemini-2.5-flash`, embeddings `gemini/gemini-embedding-001` @ 1536 dims; response shape unchanged). `commits.summary` now filled by the LLM (fail-open → null on provider outage; diff parsing stays deterministic). `GET /projects/:id/context` now returns real `recent_decisions` (newest 5, `{id, text, created_at}`) and `relevant_contracts` (newest 5, `{route, method}`) — shape unchanged, same data now feeds `get_project_context()` via MCP. New OPTIONAL env vars: `SCAFFOLD_LLM_MODEL`, `SCAFFOLD_EMBED_MODEL` (engine — override the Gemini defaults; no change to the frozen list). Retrieval: pgvector cosine search over new nullable `embedding` columns with deterministic keyword/recency fallback; `/reason` returns 503 without `SCAFFOLD_TEAM_LLM_KEY`, 502 on provider error.
- 2026-09-24 — Day 4 Part B: conflict detection + auto-GitHub-issue added (engine only, no route renames). `POST /projects/:id/contracts` and `POST /projects/:id/github-webhook` now run deterministic conflict detection (repo rule #3; new `engine/app/services/conflict_service.py`) against the project's registered contracts BEFORE the write: on a hit they write a `conflict_flagged` event, upsert a deduped `blockers` row, and best-effort file a GitHub issue (new optional env var `SCAFFOLD_GITHUB_REPO`="owner/repo", default `themdmohsin/Scaffold`; requires `GITHUB_TOKEN`; fail-open — issue failure never blocks ingestion). ADDITIVE response keys: POST /contracts may include `"conflicts": {events, blockers, issues}` when findings exist (201 status unchanged); webhook `processed[]` entries may include `"conflicts": <count>`. Detection rules: `conflicting_shape` (same method+route, schemas diverge — both sides must declare schemas), `method_divergence` (same normalized path, different method), `sibling_collision` (warning — same 2-segment feature prefix, different path). Pure re-registrations are NOT conflicts. No schema changes (uses existing `blockers` table + `conflict_flagged` event type).

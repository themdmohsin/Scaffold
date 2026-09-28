# Prompt for the next Scaffold session (paste this whole file into OpenCode)

You are taking over **Scaffold** — a hackathon project (6-day build plan, forked OpenCode client) that gives every developer's AI coding agent live shared awareness of the team's project: tasks, decisions, API contracts, conflicts. It is Day 6+; the build is functionally complete and demo'd end to end on one host. Your job is a short, specific list of leftovers. Read this fully before touching anything.

## 0. Read these first (in order)

1. `AGENTS.md` (repo root) — frozen naming contracts, repo rules, build/test commands. **The contracts are frozen: 10 DB tables, 10 API routes, 6 MCP tool names, exact env var names. Never rename anything.**
2. `docs/HANDOFF.md` — top entry (2026-09-28) is the live state; older entries are history.
3. `docs/scaffold-build-plan.md` — the 6-day plan (§17 = the demo script, §2 = per-day requirements).
4. `docs/scaffold-technical-research.md` — why the architecture is what it is.
5. `docs/API_CONTRACTS.md` + `docs/SCHEMA.md` — the frozen contracts (append-only; changelog entries at bottom).
6. `docs/day5-integration-runbook.md` — the two-laptop pass checklist (go/no-go boxes).
7. `docs/demo-conflict-scenario.md` — the rehearsed conflict demo beat.

## 1. Where the project stands (verified facts)

- **Engine** (`engine/`, FastAPI): all 10 routes live, MCP server at `/mcp` (6 tools), pgvector retrieval (1536-dim), LiteLLM→Gemini reasoning in `services/reasoning.py` (the ONLY file allowed to call an LLM), deterministic conflict detection (`services/conflict_service.py`) that files real GitHub issues, webhook ingests main-branch pushes via `diff_parser.py` (deterministic — the LLM never decides what changed).
- **Dashboard** (`dashboard/`, React+Vite): task board, decision log, contracts, ask-`/reason` flow with one-click suggestion→assigned-task, Supabase Realtime live pill (INSERT + DELETE both proven live).
- **Plugin** (`opencode-plugin/.opencode/plugins/scaffold.ts`): speaks MCP to the engine — injects bounded project context into every system prompt, pins registered API contracts for routes the agent is about to write, reports every write/edit back via `report_change`, parks-and-replays reports when the engine is down. Verified 55/55 offline + 17/17 vs real MCP SDK + 3/3 live MCP acceptance.
- **Suites last green:** day2 31, day3 35, day4b 25, day5 74 → 165/165 against live Supabase. Dashboard build green.
- **Demo verdict on record: GO** (Day-6 rehearsal, 3/3 passes, backup video recorded).

## 2. Git state (do this first)

- Branch `day6-mk-retry-and-verification` is pushed; **PR #6 is OPEN against `main`** (fixes: realtime DELETE bindings, StrictMode double-subscribe, HANDOFF entries). **Merge PR #6 first**, pull main, then branch from it: `day7-<initials>-<short-feature>`. Small commits, PR same day.
- Working tree should be clean. `docs/next-session-prompt.md` (this file) is uncommitted — commit it on your new branch if you want to keep it.

## 3. What is LEFT (your actual todo list, in priority order)

1. **Prove a real OpenCode session through the plugin — the one unproven leg.** Everything else is verified; the actual agent loop never ran. Recipe: scratch worktree (`git init`, copy `opencode-plugin/.opencode/plugins/scaffold.ts` into `.opencode/plugins/`), `SCAFFOLD_ENGINE_URL=http://localhost:8000`, launch `cd opencode-plugin/opencode && bun run --cwd packages/opencode src/index.ts run --model <claude-model> "<prompt that writes a small routes_echo.py with POST /api/echo>"`. Known trap: a previous headless run **hung silently** (300s, no output, no file, no event) — if it hangs, check fork logs under `~/.local/share/opencode/log/`, try TUI mode instead of `run`, and check the plugin loaded (engine-side MCP session will show in the uvicorn log the moment the plugin handshakes). Success = engine log/DB shows `change_reported` event with `wrote routes_echo.py`, and a SECOND session's injected context block mentions it. Debug with `bun run ... --print-logs` or `OPENCODE_LOG_LEVEL=DEBUG` if available.
2. **Add a model fallback chain in `services/reasoning.py`** (the only LLM file): try configured model → on 404/503 walk a small candidate list (`gemini/gemini-3.1-flash-lite`, `gemini/gemini-3.7-flash`, `gemini/gemini-3.8-flash`) and cache the winner per process. Context: the rotated Google key is a "new user" key — `gemini-2.5-flash` 404s for it, `gemini-3.6-flash` saturates (503 storms), `gemini-3.1-flash-lite` currently works and is set via `SCAFFOLD_LLM_MODEL` in `engine/.env`. Embeddings stay `gemini/gemini-embedding-001` @ 1536 dims (do not touch the dimension). Pin with tests in the existing script-style suites.
3. **Reset the demo project to pristine** before any demo: project `fdcb7511-434b-40c1-a391-0cd45e2150a5` has dry-run artifacts (2 extra `POST /api/auth/login` contracts, a `[CONFLICT]` blocker + `conflict_flagged` event, task "Implement password-reset logic", user "Laptop B (dry-run)", some `change_reported` events). Delete exactly those rows (tasks/contracts/blockers/events for that project) — keep the 2 original tasks, the FastAPI decision, and the 3 original contracts (`/api/test/scaffold`, `/api/test/scaffold-v2`, `/api/payments/checkout`).
4. **Optional, user-side**: GitHub PAT needs *Issues: read and write* on themdmohsin/Scaffold — issue #5 stays open until then (the token can create but not comment/close). Close #5 once fixed. Also consider merging PR #6 → delete branch.
5. **The two-real-laptops pass** (`docs/day5-integration-runbook.md`): engine host runs `uvicorn app.main:app --host 0.0.0.0 --port 8000` (already the local default now), laptop 2 clones repo + `cd opencode-plugin/opencode && bun install --ignore-scripts`, sets `SCAFFOLD_ENGINE_URL=http://<engine-lan-ip>:8000`, joins via `POST /projects/:id/invite` → `POST /projects/join` (no SQL seeding). Check off the go/no-go boxes for real. Firewall/Tailscale note in the runbook.

## 4. Environment facts (Windows machine)

- Python 3.14, bun 1.4.2. Engine deps installed globally (`uvicorn`, `litellm`, `pgvector`, psycopg). Run suites from `engine/`: `python -m tests.test_day2` / `test_day3` / `test_day4b` / `test_day5` (script-style, print PASS/FAIL, exit non-zero on failure; no pytest).
- `engine/.env` exists with real keys (DATABASE_URL→Supabase pooler, SCAFFOLD_TEAM_LLM_KEY (Google `AQ.A…` format — litellm accepts it as-is), GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, SCAFFOLD_DEFAULT_PROJECT_ID, SCAFFOLD_LLM_MODEL). **Never print, copy, or commit any key; `.env` files must never be read into context — modify only via targeted scripts and never echo secrets.**
- Dashboard `.env` has the 3 VITE_ vars (Supabase URL must be the bare `https://<ref>.supabase.co`, NOT `.../rest/v1` — that breaks the realtime websocket).
- Fork auth: `anthropic`, `google`, `openai` providers are already logged in (stored auth) — the developer's Claude key goes through the fork's own routing, NEVER through `SCAFFOLD_TEAM_LLM_KEY` (repo rule #1: the team key pays only for engine-internal reasoning, never a developer's coding session).
- Local Supabase project ref: `vduonbvknuyxyxnwqwssm` (realtime publication `supabase_realtime` already has tasks/decisions/events — verified in Postgres, no GUI action needed).

## 5. Non-negotiable repo rules (from AGENTS.md — full list there)

- LLM calls ONLY in `engine/app/services/reasoning.py`. Deterministic logic (diff parsing, conflict detection, roster/availability, retry classification) stays plain Python/SQL.
- Git diffs decide what changed; the LLM only summarizes. Never store file contents in the DB — paths + one-line summaries only. Context blocks sent to any LLM stay small and bounded (≤ ~2400 chars).
- Any hook in the plugin must fail open (never throw into the coding session).
- Append-only docs: SCHEMA.md / API_CONTRACTS.md get changelog entries at the bottom, never rewrites. End every session by prepending a dated entry to `docs/HANDOFF.md` (what's built, new env vars, what's broken, next steps).
- Don't attempt real Git merge-conflict resolution in the product — Scaffold prevents *semantic* conflicts only.

## 6. Suggested first hour

1. Merge PR #6, pull main, new branch.
2. Launch the engine (`cd engine && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`) and confirm `/health`.
3. Attack item 3.1 (the real fork session). It is the demo's most important unproven beat and everything else is cleanup by comparison.
4. Then the fallback chain (3.2), then demo reset (3.3), then run `python -m tests.test_day3` and `test_day5` to confirm you left the engine green, then HANDOFF entry.

If something fundamental conflicts with this brief, trust the docs in-repo and say so in the HANDOFF entry rather than improvising silently.

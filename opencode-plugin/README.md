# opencode-plugin

The Scaffold client layer: the forked OpenCode source + the Scaffold hook plugin.

## Layout

- `opencode/` — the forked OpenCode monorepo (source of truth: github.com/sst/opencode). The visible rebrand (CLI banner, terminal titles) lives in `packages/opencode/src/cli/ui.ts` and `packages/tui/src/app.tsx`.
- `.opencode/plugins/scaffold.ts` — the Scaffold plugin (hooks → engine).

## Run the fork from source

```bash
cd opencode-plugin/opencode
bun install --ignore-scripts   # tree-sitter native builds fail on Windows; they're optional
bun run --cwd packages/opencode src/index.ts            # = the "scaffold" CLI
bun run --cwd packages/opencode src/index.ts --version  # prints "local"
```

## Load the plugin

OpenCode auto-loads plugins from `.opencode/plugins/` of the project you open.
To try it: run the CLI from a directory containing `.opencode/plugins/scaffold.ts`
(this repo's `opencode-plugin/` works), with `SCAFFOLD_ENGINE_URL` set.

## What the plugin does (Day 4)

The plugin talks to the engine over MCP (streamable HTTP at `<SCAFFOLD_ENGINE_URL>/mcp`),
calling the frozen tool names from `docs/API_CONTRACTS.md` verbatim — no SDK dependency,
just a small JSON-RPC client so the file stays copy-pasteable.

| hook | what it does |
| --- | --- |
| `event` (`session.created`) | warms `get_project_context()` so the first prompt is instant, toasts on success |
| `chat.message` | marks the cached context stale — the next request re-reads shared state |
| `experimental.chat.system.transform` | **injects the bounded project context block** into the system prompt (the fork cannot add prompt text from `tool.execute.before`; this is the real injection point) |
| `tool.execute.before` | for `write`/`edit`/`apply_patch`, extracts `/api/...`-style routes from the arguments and pins `get_api_contract(route)` into the next request |
| `tool.execute.after` | for `write`/`edit`/`apply_patch`, reports `report_change(diff_summary, files_changed)` built from the tool result's real additions/deletions |
| `experimental.session.compacting` | keeps the context block across compaction |
| `shell.env` | injects `SCAFFOLD_ENGINE_URL` / `SCAFFOLD_PROJECT_DIR` |

Project resolution: the plugin never sends a `project_id`; the engine falls back to
`SCAFFOLD_DEFAULT_PROJECT_ID` (the frozen single-project convention).

Guarantees: hooks **never throw** (the fork's `plugin.trigger` has no error handling, so a
throw would abort the tool call), calls time out after 2.5s, repeated engine failures back
off for 60s, reports the engine refuses are parked (max 50, kept 1h) and replayed oldest-first
when it recovers, and only file paths + one-line summaries ever leave the machine — never source
contents (repo rule #4) and never an LLM guess about what changed (rule #2).

## Verify the plugin

```bash
cd opencode-plugin
npm install                                                    # one-time: TypeScript + the plugin's real type deps
npm run typecheck                                              # strict tsc against the vendored plugin types
node tests/verify_plugin.ts                                    # 55 checks, fake engine, no deps
python tests/mcp_sdk_interop.py                                # 17 checks vs the real mcp SDK
python tests/acceptance_live.py --self-test                    # 4 checks: the acceptance runner works
```

`npm run typecheck` resolves `@opencode-ai/plugin` to the **vendored** source in
`opencode/packages/plugin` (version 1.18.32, the same as the published package), so the hook
signatures checked are the ones the forked CLI actually calls. It is strict and covers both the
plugin and `tests/`, and `npm install` here is unrelated to the fork's bun install — the two never
meet (`node_modules/` is gitignored).

`verify_plugin.ts` drives every hook against a fake-but-wire-faithful engine (MCP handshake,
session id, JSON *and* SSE replies, a hanging call, transient engine errors and recovery,
engine-down) and asserts the block caps, the report payloads and that no source text ever leaves. Node 22.18+ strips TS types natively,
so there is no build step.

`mcp_sdk_interop.py` closes the loop that a fake server cannot: it serves fixture tools through
the **real** `mcp` SDK using the engine's own mount code (`streamable_http_app()` +
`_McpPathFix`, imported from `engine/app/main.py`, not copied) and asserts on the server side
what the plugin actually sent — the three frozen tool names, no `project_id`, the route from
the write arguments, and a worktree-relative diff summary. It needs no Postgres and no LLM; it
re-executes itself with `engine/.venv`'s interpreter if `mcp` is not importable.

The real engine's server side of the same protocol is covered by
`engine/tests/test_day2.py`.

## Run the real engine check (Day 4 acceptance)

Everything above uses a fixture engine. To check the plugin against a **live** engine — real
Postgres, real project state — start the engine (`engine/.env` with `DATABASE_URL` and
`SCAFFOLD_DEFAULT_PROJECT_ID`, schema applied, `uvicorn app.main:app`) and run:

```bash
python tests/acceptance_live.py                    # $SCAFFOLD_ENGINE_URL, else localhost:8000
python tests/acceptance_live.py --url http://192.168.1.20:8000
SCAFFOLD_ACCEPTANCE_REPORT=1 python tests/acceptance_live.py   # also exercises report_change
```

No LLM key is needed and no coding session is started: it drives the same hooks OpenCode calls and
prints the block the agent would receive, verbatim — paste that into `docs/HANDOFF.md` as the Day 4
evidence. `SCAFFOLD_ACCEPTANCE_REPORT=1` additionally writes a `change_reported` event, which is why
it is opt-in.

The interactive part still needs the fork itself (`bun install --ignore-scripts` in `opencode/`)
and a provider key for a real coding session:

1. `SCAFFOLD_ENGINE_URL=http://<engine>:8000 bun run --cwd packages/opencode src/index.ts` from this directory
2. ask the agent to build an endpoint that already has a registered contract — the very first request should carry that contract
3. have a teammate commit a change to the same project, then ask again — the block should show the new decision/contract without a restart
4. the dashboard's event feed should show the `change_reported` event and a toast should appear in the TUI

## Pending (tracked in docs/HANDOFF.md)

- Two-machine loop test: Dev A commits → Dev B's next prompt carries the right context.
- `report_change` only fires for file-writing tools; shell/git mutations are not reported yet.
- Identity rename: binary name, npm package names, config paths (`~/.config/opencode`) — deferred deliberately. Visible rebrand is complete (CLI banner, terminal titles, TUI home logo).

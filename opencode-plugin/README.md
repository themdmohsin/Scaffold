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

Day 1 the plugin only structured-logs hook fires (`service: "scaffold"`).
Day 4 wires the real engine calls (context injection + change reporting).

## Pending (tracked in docs/HANDOFF.md)

- Identity rename: binary name, npm package names, config paths (`~/.config/opencode`), TTY graphic glyphs (`packages/tui/src/logo.ts`) — deferred deliberately.

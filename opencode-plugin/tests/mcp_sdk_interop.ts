/**
 * Driver for `tests/mcp_sdk_interop.py` — it runs the plugin's real hooks against a REAL
 * `mcp`-SDK server (fixture tools, no Postgres), started by the Python side.
 *
 * Not runnable on its own: the runner sets SCAFFOLD_ENGINE_URL and INTEROP_FIXTURES, then
 * reads the `__RESULT__` line this driver prints. Assertions about what actually arrived at
 * the server (and that no source text ever did) live on the Python side.
 */

const fixtures = JSON.parse(process.env.INTEROP_FIXTURES ?? "{}")
const engineUrl = process.env.SCAFFOLD_ENGINE_URL ?? ""
if (!engineUrl) {
  console.error("SCAFFOLD_ENGINE_URL is required (set by mcp_sdk_interop.py)")
  process.exit(2)
}

const { ScaffoldPlugin } = await import(new URL("../.opencode/plugins/scaffold.ts", import.meta.url).href)

const checks: Array<{ name: string; ok: boolean; detail?: string }> = []
const check = (name: string, cond: unknown, detail = "") =>
  checks.push({ name, ok: cond === true, detail: cond === true ? "" : detail })

const logs: any[] = []
const toasts: any[] = []
const client = {
  app: { log: async ({ body }: any) => void logs.push(body) },
  tui: { showToast: async ({ body }: any) => void toasts.push(body) },
}
const WORKTREE = process.cwd()
const hooks = await ScaffoldPlugin({
  client,
  project: { id: "opencode-project-hash" },
  directory: WORKTREE,
  worktree: WORKTREE,
  $: async () => {},
} as any)

/** Only what the plugin appended: `system[length-1]` would be the base prompt when nothing
 *  was injected, which turns "contains X" checks into silent no-ops. */
const systemBlock = async () => {
  const system: string[] = ["base system prompt"]
  const baseEntries = system.length
  await hooks["experimental.chat.system.transform"]!({ sessionID: "ses-interop" }, { system } as any)
  const appended = system.slice(baseEntries)
  return { block: appended.join("\n\n"), appended: appended.length }
}

// 1. session start → context pulled over the real protocol (initialize + session id + SSE)
await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-interop" } } } as any)
const first = await systemBlock()
check(
  "context block injected from the real MCP server",
  first.appended === 1 && first.block.includes(fixtures.projectName),
  JSON.stringify(first.block.slice(0, 120)),
)
check("block carries the fixture decision", first.block.includes(fixtures.decisionText))
check("session start toasted the loaded context", toasts.some((t) => /context loaded/.test(t.message)))

// 2. a user message that names the route, then a write to it → get_api_contract(route)
await hooks["chat.message"]!({ sessionID: "ses-interop" } as any)
const SOURCE = fixtures.sourceMarker
await hooks["tool.execute.before"]!(
  { tool: "write", sessionID: "ses-interop", callID: "call-1" },
  {
    args: {
      filePath: `${WORKTREE}/engine/app/routes/interop.py`,
      content: `@router.post("${fixtures.route}")\ndef interop():\n    # ${SOURCE}\n    pass\n`,
    },
  },
)
const pinned = await systemBlock()
check(
  "pinned contract reaches the next request",
  pinned.appended === 1 && pinned.block.includes(`${fixtures.method} ${fixtures.route}`),
  pinned.block.slice(-200),
)
check("pinned contract carries the registered request shape", pinned.block.includes(fixtures.schemaMarker))

// 3. a real edit → report_change over the real protocol
await hooks["tool.execute.after"]!(
  { tool: "edit", sessionID: "ses-interop", callID: "call-2", args: { filePath: `${WORKTREE}/engine/app/routes/interop.py` } },
  {
    title: "engine/app/routes/interop.py",
    output: "Edit applied successfully.",
    metadata: { filediff: { file: `${WORKTREE}/engine/app/routes/interop.py`, additions: 7, deletions: 2 } },
  } as any,
)

// 4. shell env
const env = { env: {} as Record<string, string> }
await hooks["shell.env"]!({ cwd: WORKTREE }, env as any)
check("shell.env still injects the frozen var", env.env.SCAFFOLD_ENGINE_URL === engineUrl)

check("no source text appears in plugin logs", !JSON.stringify(logs).includes(SOURCE))
check("no warnings while the engine is healthy", logs.every((l) => l.level !== "warn"), JSON.stringify(logs.filter((l) => l.level === "warn")))

console.log(`__RESULT__${JSON.stringify({ checks, logged: logs.length, toasts: toasts.length })}`)
// Undici keeps sockets alive; the runner needs a clean exit.
process.exit(checks.every((c) => c.ok) ? 0 : 1)

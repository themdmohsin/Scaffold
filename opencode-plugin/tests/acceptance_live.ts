/**
 * The evidence driver for `tests/acceptance_live.py`: what a real coding agent would actually
 * receive from a **live** Scaffold engine, printed verbatim so it can be pasted into HANDOFF.md.
 *
 * Not runnable on its own — the runner sets SCAFFOLD_ENGINE_URL and reads the `__RESULT__` line.
 * Set SCAFFOLD_ACCEPTANCE_REPORT=1 to also exercise the report_change round trip (that WRITES a
 * `change_reported` event into the real project, so it is opt-in).
 */

const engineUrl = process.env.SCAFFOLD_ENGINE_URL ?? "http://localhost:8000"
const shouldReport = process.env.SCAFFOLD_ACCEPTANCE_REPORT === "1"

const { ScaffoldPlugin } = await import(new URL("../.opencode/plugins/scaffold.ts", import.meta.url).href)

const logs: any[] = []
const toasts: any[] = []
const client = {
  app: { log: async ({ body }: any) => void logs.push(body) },
  tui: { showToast: async ({ body }: any) => void toasts.push(body) },
}
const cwd = process.cwd()
const hooks = await ScaffoldPlugin({
  client,
  project: { id: "acceptance-check" },
  directory: cwd,
  worktree: cwd,
  $: async () => {},
} as any)

// Exactly what OpenCode does at the start of a session.
await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-acceptance" } } } as any)
const system = ["base system prompt"]
const baseEntries = system.length
await hooks["experimental.chat.system.transform"]!({ sessionID: "ses-acceptance" }, { system } as any)
// Only entries APPENDED by the plugin count — the base prompt is not an injected block.
const block = system.length > baseEntries ? system.slice(baseEntries).join("\n\n") : ""

console.log("----- injected block (verbatim) -----")
console.log(block || "(nothing injected)")
console.log("----- end injected block -----")

const warnings = logs.filter((l) => l.level === "warn")
for (const warning of warnings) console.error(`warning: ${warning.message}`)

let reported = false
if (shouldReport) {
  // A write to a clearly synthetic path, so nobody mistakes this for real work in the feed.
  await hooks["tool.execute.before"]!(
    { tool: "write", sessionID: "ses-acceptance", callID: "acceptance-write" },
    { args: { filePath: `${cwd}/acceptance-check.txt`, content: "scaffold acceptance check\n" } },
  )
  await hooks["tool.execute.after"]!(
    {
      tool: "write",
      sessionID: "ses-acceptance",
      callID: "acceptance-write",
      args: { filePath: `${cwd}/acceptance-check.txt`, content: "scaffold acceptance check\n" },
    },
    { title: "acceptance-check.txt", output: "", metadata: { filepath: `${cwd}/acceptance-check.txt`, exists: false } } as any,
  )
  reported = toasts.length > 0
}

console.log(
  `__RESULT__${JSON.stringify({
    engineUrl,
    blockChars: block.length,
    warnings: warnings.map((w) => w.message),
    reportsAttempted: shouldReport,
    reportsToasted: reported,
  })}`,
)
process.exit(0)

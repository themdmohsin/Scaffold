/**
 * Day 4 verification — the Scaffold OpenCode plugin against a fake-but-faithful engine.
 *
 * Run from opencode-plugin/:  node tests/verify_plugin.ts
 * (Node 22.18+ strips TypeScript types natively; no build step, no bun required.)
 *
 * What is REAL here: every line of `.opencode/plugins/scaffold.ts` — the MCP streamable-HTTP
 * client (initialize handshake, session id, SSE + JSON replies), the context block, the
 * contract pinning, the report_change payloads and the fail-open behaviour.
 * What is FAKE here: the engine. The wire format mirrors `engine/app/mcp_server.py`, whose
 * real-server behaviour is already covered by `engine/tests/test_day2.py` (real Postgres).
 */

import http from "node:http"

const PASS: string[] = []
const FAIL: string[] = []

function check(name: string, cond: boolean, extra = ""): void {
  ;(cond ? PASS : FAIL).push(name)
  console.log(`  ${cond ? "PASS" : "FAIL"}  ${name}${!cond && extra ? `  [${extra}]` : ""}`)
}

const WORKTREE = "/repo/scaffold"

const FIXTURE_CONTEXT = {
  project: {
    id: "fdcb7511-434b-40c1-a391-0cd45e2150a5",
    name: "Scaffold Demo",
    goal: "Give teammates' agents shared awareness",
    deadline: "2026-09-30T18:00:00+00:00",
    created_at: "2026-09-23T10:00:00+00:00",
  },
  tasks: { todo: 3, in_progress: 2, done: 1 },
  active_tasks: Array.from({ length: 11 }, (_, i) => ({
    id: `task-${i}`,
    title: i === 8 ? "TASK_EIGHT_SHOULD_BE_CAPPED" : `Task number ${i}`,
    status: "in_progress",
    owner_id: null,
    due_at: null,
  })),
  recent_decisions: [
    { id: "d1", text: "Auth lives at POST /api/auth/login returning { token }", created_at: "2026-09-24T10:00:00+00:00" },
    { id: "d2", text: "Decision two", created_at: "2026-09-24T10:00:00+00:00" },
    { id: "d3", text: "Decision three", created_at: "2026-09-24T10:00:00+00:00" },
    { id: "d4", text: "Decision four", created_at: "2026-09-24T10:00:00+00:00" },
    { id: "d5", text: "Decision five", created_at: "2026-09-24T10:00:00+00:00" },
    { id: "d6", text: "DECISION_SIX_SHOULD_BE_CAPPED", created_at: "2026-09-24T10:00:00+00:00" },
  ],
  relevant_contracts: [{ route: "/api/auth/session", method: "GET" }],
  generated_at: "2026-09-25T09:00:00+00:00",
}

const FIXTURE_CONTRACTS: Record<string, any> = {
  "/api/auth/login": {
    found: true,
    id: "c1",
    route: "/api/auth/login",
    method: "POST",
    request_schema: { email: "string", password: "string" },
    response_schema: { token: "string", expires_in: "number" },
    created_at: "2026-09-24T10:00:00+00:00",
  },
}

type Recorded = { method: string; tool?: string; args?: any; sessionHeader?: string | undefined }

/** Controllable fake engine: `mode` toggles SSE replies and hanging calls mid-test. */
const engine = {
  requests: [] as Recorded[],
  reports: [] as any[],
  mode: { sse: true, hang: "" as string, sessions: 0, errorContext: false, emptyContext: false },
  // Wire-faithful session store: initialize issues "sess-1"; a call with an unknown
  // id is answered 404, exactly like the real streamable-HTTP server.
  sessions: new Set<string>(["sess-1"]),
  /** Wire-faithful restart: session ids are forgotten; recorded traffic survives. */
  restart: () => engine.sessions.clear(),
}

const server = http.createServer((req, res) => {
  let raw = ""
  req.on("data", (chunk) => (raw += chunk))
  req.on("end", () => {
    const message = JSON.parse(raw || "{}")
    const sessionHeader = req.headers["mcp-session-id"] as string | undefined
    // Streamable-HTTP ground truth (engine/tests/test_day2.py): calls with a session id
    // the server does not know get a bare 404 — that is how a restarted engine answers.
    if (message.method !== "initialize" && !engine.sessions.has(sessionHeader as string)) {
      res.writeHead(404)
      return res.end()
    }
    if (message.method !== "notifications/initialized") {
      engine.requests.push({
        method: message.method,
        tool: message.params?.name,
        args: message.params?.arguments,
        sessionHeader,
      })
    }

    const reply = (payload: any) => {
      engine.mode.sse = !engine.mode.sse // exercise both reply shapes
      const body = JSON.stringify(payload)
      if (message.method === "notifications/initialized") {
        res.writeHead(202, { "mcp-session-id": "sess-1" })
        return res.end()
      }
      if (engine.mode.sse) {
        res.writeHead(200, {
          "content-type": "text/event-stream",
          "mcp-session-id": "sess-1",
        })
        return res.end(`event: message\ndata: ${body}\n\n`)
      }
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sess-1" })
      res.end(body)
    }

    if (message.method === "initialize") {
      engine.mode.sessions += 1
      engine.sessions.add("sess-1")
      return reply({
        jsonrpc: "2.0",
        id: message.id,
        result: {
          protocolVersion: "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "scaffold", version: "fake-0.1.0" },
        },
      })
    }

    if (message.method === "notifications/initialized") return reply({})

    if (message.method === "tools/call") {
      const name = message.params?.name
      if (engine.mode.hang === name) return // never answer: exercises the client timeout
      if (name === "get_project_context") {
        if (engine.mode.errorContext) {
          return reply({ jsonrpc: "2.0", id: message.id, error: { code: -32000, message: "project not found" } })
        }
        if (engine.mode.emptyContext) return reply({ jsonrpc: "2.0", id: message.id, result: toolText(null) })
        return reply({ jsonrpc: "2.0", id: message.id, result: toolText(FIXTURE_CONTEXT) })
      }
      if (name === "get_api_contract") {
        const route = message.params?.arguments?.route
        const found = FIXTURE_CONTRACTS[route] ?? { found: false, route }
        return reply({ jsonrpc: "2.0", id: message.id, result: toolText(found) })
      }
      if (name === "report_change") {
        engine.reports.push(message.params?.arguments)
        return reply({
          jsonrpc: "2.0",
          id: message.id,
          result: { structuredContent: { ok: true, event_id: "evt-1" } },
        })
      }
      return reply({
        jsonrpc: "2.0",
        id: message.id,
        result: { content: [{ type: "text", text: JSON.stringify({ ok: false, error: "unknown tool" }) }] },
      })
    }

    reply({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: `unknown method ${message.method}` } })
  })
})

const toolText = (value: any) => ({ content: [{ type: "text", text: JSON.stringify(value) }] })

const port: number = await new Promise((resolve) => {
  server.listen(0, "127.0.0.1", () => resolve((server.address() as any).port))
})
process.env.SCAFFOLD_ENGINE_URL = `http://127.0.0.1:${port}`

const { ScaffoldPlugin } = await import(new URL("../.opencode/plugins/scaffold.ts", import.meta.url).href)

// ---------------------------------------------------------------------------
// Fake OpenCode side
// ---------------------------------------------------------------------------
const logs: any[] = []
const toasts: any[] = []
const client = {
  app: { log: async ({ body }: any) => void logs.push(body) },
  tui: { showToast: async ({ body }: any) => void toasts.push(body) },
}
const pluginInput = {
  client,
  project: { id: "opencode-project-hash" },
  directory: `${WORKTREE}/opencode-plugin`,
  worktree: WORKTREE,
  $: async () => {},
} as any

const hooks = await ScaffoldPlugin(pluginInput)
/** Returns ONLY what the plugin appended — measuring `system[length-1]` would count the base
 *  prompt and make "the block stays bounded" pass vacuously when injection is broken. */
const systemHook = async (sessionID?: string) => {
  const system: string[] = ["base system prompt"]
  const baseEntries = system.length
  await hooks["experimental.chat.system.transform"]!({ sessionID }, { system } as any)
  const appended = system.slice(baseEntries)
  return { system, block: appended.join("\n\n"), appended: appended.length }
}
const before = (tool: string, args: any, sessionID = "ses-1", callID = `call-${Math.random()}`) =>
  hooks["tool.execute.before"]!({ tool, sessionID, callID }, { args })
const after = (tool: string, args: any, metadata: any, sessionID = "ses-1", callID = `call-${Math.random()}`) =>
  hooks["tool.execute.after"]!(
    { tool, sessionID, callID, args },
    { title: "", output: "", metadata } as any,
  )

const callsTo = (tool: string) => engine.requests.filter((r) => r.tool === tool)

console.log("\n== hook surface ==")
check(
  "all Day 4 hooks registered",
  ["event", "chat.message", "experimental.chat.system.transform", "tool.execute.before", "tool.execute.after", "experimental.session.compacting", "shell.env"].every(
    (name) => typeof (hooks as any)[name] === "function",
  ),
  Object.keys(hooks).join(","),
)

console.log("\n== session start → context warm-up ==")
await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-1" } } } as any)
check("session.created pulls get_project_context", callsTo("get_project_context").length === 1)
check(
  "MCP handshake completed once and session id reused",
  engine.mode.sessions === 1 &&
    callsTo("get_project_context")[0].sessionHeader === "sess-1" &&
    callsTo("get_project_context")[0].args?.project_id === undefined,
  JSON.stringify(callsTo("get_project_context")[0]),
)
check("toast surfaced the loaded context", toasts.some((t) => /context loaded/.test(t.message)))

console.log("\n== injection into the session ==")
const first = await systemHook("ses-1")
const block = first.block
check(
  "system prompt grew by exactly one block",
  first.appended === 1 && first.system[0] === "base system prompt",
  JSON.stringify(first.system),
)
check("block names the project and goal", block.includes("Scaffold Demo") && block.includes("shared awareness"))
check("block carries task counts", block.includes("3 todo"))
check("block carries recent decisions", block.includes("/api/auth/login returning { token }"))
check("block carries registered contracts", block.includes("GET /api/auth/session"))
check("active tasks capped at 8", !block.includes("TASK_EIGHT_SHOULD_BE_CAPPED"))
check("decisions capped at 5", !block.includes("DECISION_SIX_SHOULD_BE_CAPPED"))
check(
  "block stays bounded (repo rule #6)",
  block.length > 0 && block.length <= 2400,
  `${block.length} chars`,
)
check("block tells the agent not to invent shapes", /do not re-litigate|match these exactly/.test(block))

const requestsBeforeCacheCheck = engine.requests.length
await systemHook("ses-1")
check("second request inside the TTL reuses the cache", engine.requests.length === requestsBeforeCacheCheck)

await hooks["chat.message"]!({ sessionID: "ses-1" } as any)
const afterMessage = await systemHook("ses-1")
check(
  "the refetched context is injected again (not just fetched)",
  afterMessage.appended === 1 && afterMessage.block.includes("Scaffold Demo"),
)
check(
  "a new user message refetches shared state",
  callsTo("get_project_context").length === 2,
  `${callsTo("get_project_context").length} fetches`,
)

console.log("\n== transient engine failure → recovery (nulls are never cached) ==")
await hooks["chat.message"]!({ sessionID: "ses-1" } as any)
const fetchesBeforeFailure = callsTo("get_project_context").length
engine.mode.errorContext = true
const failedFetch = await systemHook("ses-1")
check("an engine error injects nothing", failedFetch.appended === 0, `${failedFetch.appended} appended`)
engine.mode.errorContext = false
const healed = await systemHook("ses-1")
check(
  "the failed fetch is not cached: the next request refetches and injects",
  healed.appended === 1 && healed.block.includes("Scaffold Demo"),
  `${healed.appended} appended`,
)
check(
  "recovery needs no new user message (the stale flag survives a failed fetch)",
  callsTo("get_project_context").length > fetchesBeforeFailure + 1,
  `${callsTo("get_project_context").length} fetches`,
)
await systemHook("ses-1")
check(
  "the cache after recovery holds a payload, not a failure marker",
  callsTo("get_project_context").length === fetchesBeforeFailure + 2,
  `${callsTo("get_project_context").length} fetches`,
)

engine.mode.emptyContext = true
await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-4" } } } as any)
engine.mode.emptyContext = false
check(
  "engine up but no usable project context is flagged as a setup problem, not an outage",
  logs.some((l) => l.level === "warn" && /no usable project context/.test(l.message)) &&
    toasts.some((t) => /no project context from engine/.test(t.message)),
)

console.log("\n== tool.execute.before → contract pinning (deterministic) ==")
const SOURCE_MARKER = "SUPER_SECRET_SOURCE_BODY_9f3a"
await before("write", {
  filePath: `${WORKTREE}/engine/app/routes/auth.py`,
  content: `@router.post("/api/auth/login")\ndef login():\n    # ${SOURCE_MARKER}\n    pass\n`,
})
check("write hook asks for the route it is about to implement", callsTo("get_api_contract").length === 1)
check(
  "get_api_contract called by exact frozen name with the route",
  callsTo("get_api_contract")[0].tool === "get_api_contract" &&
    callsTo("get_api_contract")[0].args?.route === "/api/auth/login",
  JSON.stringify(callsTo("get_api_contract")[0].args),
)
check(
  "no source text leaves the machine in the before hook",
  !JSON.stringify(engine.requests).includes(SOURCE_MARKER),
)

const withPin = await systemHook("ses-1")
const pinnedBlock = withPin.block
check(
  "pinned contract is injected on the next request",
  withPin.appended === 1 && pinnedBlock.includes("POST /api/auth/login"),
  `${withPin.appended} appended`,
)
check("pinned contract carries the request shape", pinnedBlock.includes("email"))
check("pinned contract carries the response shape", pinnedBlock.includes("token"))

await before("write", { filePath: `${WORKTREE}/a.ts`, content: `fetch("/api/auth/login")` })
check("an already-pinned route is not looked up twice", callsTo("get_api_contract").length === 1)

await before("read", { filePath: `${WORKTREE}/engine/app/routes/auth.py` })
await before("shell", { command: `grep /api/auth/login -r .` })
check("read-only tools never hit the contract lookup", callsTo("get_api_contract").length === 1)

console.log("\n== tool.execute.after → report_change ==")
await after("edit", { filePath: `${WORKTREE}\\engine\\app\\routes\\auth.py` }, {
  filediff: { file: `${WORKTREE}/engine/app/routes/auth.py`, additions: 12, deletions: 3 },
})
check("edit reports to report_change", engine.reports.length === 1)
check(
  "paths are worktree-relative, engine-style",
  JSON.stringify(engine.reports[0]?.files_changed) === JSON.stringify(["engine/app/routes/auth.py"]),
  JSON.stringify(engine.reports[0]),
)
check(
  "summary is deterministic diff metadata, not an LLM guess",
  /edited engine\/app\/routes\/auth\.py \(\+12 -3 lines\)/.test(engine.reports[0]?.diff_summary ?? ""),
  engine.reports[0]?.diff_summary,
)
check(
  "report payload carries no file contents (repo rule #4)",
  !JSON.stringify(engine.reports[0]).includes(SOURCE_MARKER) &&
    !("content" in (engine.reports[0] ?? {})) &&
    !("patch" in (engine.reports[0] ?? {})),
)

// 55 UTF-8 bytes (é=2, 🌍=4, 中/文=3 each) but only 48 JS string units — a `.length`-based
// summary would misreport this as "48 bytes".
await after(
  "write",
  { filePath: `${WORKTREE}/dash/src/auth.ts`, content: `// ${SOURCE_MARKER}\n// héllo 🌍 中文\n` },
  { filepath: `${WORKTREE}/dash/src/auth.ts`, exists: false },
)
check(
  "write summary counts real UTF-8 bytes (not UTF-16 code units), never contents",
  JSON.stringify(engine.reports[1]?.files_changed) === JSON.stringify(["dash/src/auth.ts"]) &&
    /created, 55 bytes/.test(engine.reports[1]?.diff_summary ?? "") &&
    !/48 bytes/.test(engine.reports[1]?.diff_summary ?? "") &&
    !JSON.stringify(engine.reports[1]).includes(SOURCE_MARKER),
  JSON.stringify(engine.reports[1]),
)

await after("apply_patch", { patchText: "*** Begin Patch" }, {
  files: [
    { filePath: `${WORKTREE}/engine/app/main.py`, relativePath: "engine/app/main.py", type: "update", additions: 4, deletions: 1 },
    { filePath: `${WORKTREE}/engine/app/routes/reason.py`, relativePath: "engine/app/routes/reason.py", type: "update", additions: 9, deletions: 0 },
  ],
})
check(
  "apply_patch reports every file it touched",
  JSON.stringify(engine.reports[2]?.files_changed) ===
    JSON.stringify(["engine/app/main.py", "engine/app/routes/reason.py"]),
  JSON.stringify(engine.reports[2]?.files_changed),
)

const repeatedCall = "call-fixed"
await after("edit", { filePath: `${WORKTREE}/a.ts` }, { filediff: { file: `${WORKTREE}/a.ts`, additions: 1, deletions: 1 } }, "ses-1", repeatedCall)
await after("edit", { filePath: `${WORKTREE}/a.ts` }, { filediff: { file: `${WORKTREE}/a.ts`, additions: 1, deletions: 1 } }, "ses-1", repeatedCall)
check("a replayed tool call does not double-report", engine.reports.length === 4, `${engine.reports.length} reports`)

await after("read", { filePath: `${WORKTREE}/a.ts` }, {})
await after("shell", { command: "npm test" }, {})
check("non-writing tools never report a change", engine.reports.length === 4)

console.log("\n== compaction + shell env ==")
const compaction = { context: [] as string[] }
await hooks["experimental.session.compacting"]!({ sessionID: "ses-1" }, compaction as any)
check("context survives compaction", compaction.context.length === 1 && compaction.context[0].includes("Scaffold Demo"))
const env = { env: {} as Record<string, string> }
await hooks["shell.env"]!({ cwd: `${WORKTREE}/engine` }, env as any)
check(
  "shell.env still injects the frozen var",
  env.env.SCAFFOLD_ENGINE_URL === `http://127.0.0.1:${port}` && env.env.SCAFFOLD_PROJECT_DIR === `${WORKTREE}/engine`,
)

console.log("\n== failure modes (hooks must fail open) ==")
engine.mode.hang = "get_api_contract"
await hooks["chat.message"]!({ sessionID: "ses-2" } as any)
const hangStart = Date.now()
let hangThrew = false
try {
  await before("write", { filePath: `${WORKTREE}/hang.ts`, content: `fetch("/api/auth/hang")` }, "ses-2")
} catch {
  hangThrew = true
}
const hangMs = Date.now() - hangStart
check("a hanging engine call does not throw into the tool call", !hangThrew)
check("the client times out instead of stalling the session", hangMs >= 2000 && hangMs < 6000, `${hangMs}ms`)
engine.mode.hang = ""

await after("edit", { filePath: `${WORKTREE}/recover.ts` }, { filediff: { file: `${WORKTREE}/recover.ts`, additions: 1, deletions: 0 } }, "ses-2")
check("the loop recovers after a timeout", engine.reports.length === 5, `${engine.reports.length} reports`)

server.closeAllConnections()
await new Promise((resolve) => server.close(() => resolve(undefined)))
const requestsWhenDown = engine.requests.length
let downThrew = false
try {
  await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-3" } } } as any)
  await before("write", { filePath: `${WORKTREE}/down.ts`, content: `fetch("/api/auth/login")` }, "ses-3")
  await after("edit", { filePath: `${WORKTREE}/down.ts` }, { filediff: { file: `${WORKTREE}/down.ts`, additions: 1, deletions: 0 } }, "ses-3")
  const system = await systemHook("ses-3")
  // The last good block (≤ TTL old) may still be served from cache; what must NOT happen
  // is a new fetch attempt or an error leaking into the session.
  check(
    "engine down: no new fetch is attempted; any injected block is the last known good one",
    engine.requests.length === requestsWhenDown &&
      (system.appended === 0 || system.block.includes("Scaffold Demo")),
    `appended=${system.appended} requests=${engine.requests.length}`,
  )
} catch {
  downThrew = true
}
check("engine down: hooks never throw into the session", !downThrew)
check("engine down: failures are logged, not silent", logs.some((l) => l.level === "warn"))
check(
  "engine down: session start says the engine is unreachable (not a missing project)",
  logs.some((l) => l.level === "warn" && /unreachable/.test(l.message)) &&
    toasts.some((t) => /engine unreachable/.test(t.message)),
)
check(
  "engine down: writes are not buffered or faked",
  engine.reports.length === 5 && engine.requests.length >= requestsWhenDown,
)

console.log("\n== parked reports survive an outage; engine restart invalidates sessions ==")
let engineBackUp = false
// A fresh engine boot: same wire format, but the session store starts empty (wire-faithful
// restart). The report_change leg of the previous handler is replaced by this one.
const outageHandler: http.RequestListener = (req, res) => {
  let raw = ""
  req.on("data", (chunk) => (raw += chunk))
  req.on("end", () => {
    const message = JSON.parse(raw || "{}")
    const sessionHeader = req.headers["mcp-session-id"] as string | undefined
    const reply = (payload: any) => {
      engine.mode.sse = !engine.mode.sse
      const body = JSON.stringify(payload)
      if (message.method === "notifications/initialized") {
        res.writeHead(202, { "mcp-session-id": "sess-1" })
        return res.end()
      }
      if (engine.mode.sse) {
        res.writeHead(200, { "content-type": "text/event-stream", "mcp-session-id": "sess-1" })
        return res.end(`event: message\ndata: ${body}\n\n`)
      }
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sess-1" })
      res.end(body)
    }
    if (message.method === "initialize") {
      engine.mode.sessions += 1
      // A restarted engine still accepts NEW sessions; it just does not know old ids.
      engine.sessions.add("sess-1")
      return reply({
        jsonrpc: "2.0",
        id: message.id,
        result: {
          protocolVersion: "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "scaffold", version: "fake-0.1.0" },
        },
      })
    }
    if (message.method === "notifications/initialized") return reply({})
    if (!engine.sessions.has(sessionHeader as string)) {
      res.writeHead(404)
      return res.end()
    }
    engine.requests.push({
      method: message.method,
      tool: message.params?.name,
      args: message.params?.arguments,
      sessionHeader,
    })
    if (message.params?.name === "report_change") {
      // Until recovery, the engine is up but cannot accept writes: a per-call error
      // (not a 404 — a restarted session self-heals via re-handshake).
      if (!engineBackUp) {
        return reply({ jsonrpc: "2.0", id: message.id, error: { code: -32000, message: "engine unavailable" } })
      }
      engine.reports.push(message.params?.arguments)
      return reply({ jsonrpc: "2.0", id: message.id, result: { structuredContent: { ok: true, event_id: "evt-retry" } } })
    }
    if (message.params?.name === "get_project_context") {
      return reply({ jsonrpc: "2.0", id: message.id, result: toolText(engineBackUp ? FIXTURE_CONTEXT : null) })
    }
    return reply({
      jsonrpc: "2.0",
      id: message.id,
      result: { content: [{ type: "text", text: JSON.stringify({ ok: false, error: "unknown tool" }) }] },
    })
  })
}
// Swap the handler and come back listening: same port, empty session store.
server.removeAllListeners("request")
server.addListener("request", outageHandler)
engine.restart()
await new Promise<void>((resolve) => server.listen(port, "127.0.0.1", () => resolve()))

// Outage leg: the engine answers but rejects every session id. The write itself happened;
// the report must be parked, not dropped — and its own replay attempt must fail quietly.
await after("edit", { filePath: `${WORKTREE}/outage.ts` }, { filediff: { file: `${WORKTREE}/outage.ts`, additions: 2, deletions: 0 } }, "ses-1", "call-outage-1")
check("a report the engine refuses is parked, not dropped", engine.reports.length === 5, `${engine.reports.length} reports`)
check(
  "the parked report is announced in the logs, not accepted silently",
  logs.some((l) => l.level === "warn" && /parked for retry/.test(l.message)),
)
// No new session has started since, so nothing else can flush it: still parked.
check("the parked report waits for a recovery point (no session start yet)", engine.reports.length === 5)

// Recovery point: the engine accepts sessions again; a new session replays the parked work.
engineBackUp = true
// The engine keeps listening; only its session store and per-call availability
// changed. (Deliberately NOT closeAllConnections() here: killing the healthy pooled
// socket would make the recovery fetch fail for transport reasons and mask what the
// plugin is supposed to prove at the recovery point.)
await hooks.event!({ event: { type: "session.created", properties: { sessionID: "ses-5" } } } as any)
check(
  "a restarted engine forces a fresh MCP handshake (and gets one)",
  engine.mode.sessions >= 2,
  `${engine.mode.sessions} handshakes`,
)
// Two reports were parked before recovery: down.ts (engine fully down) and outage.ts
// (engine up but refusing sessions). The flush replays them oldest first.
check(
  "the parked reports are replayed after recovery (oldest first)",
  engine.reports.length === 7 &&
    engine.reports[5]?.diff_summary === "edited down.ts (+1 -0 lines)" &&
    engine.reports[6]?.diff_summary === "edited outage.ts (+2 -0 lines)",
  JSON.stringify(engine.reports.slice(5)),
)
check(
  "the replay used a live session id, not the pre-outage one",
  engine.requests.filter((r) => r.tool === "report_change").at(-1)?.sessionHeader === "sess-1",
)

await after("edit", { filePath: `${WORKTREE}/outage2.ts` }, { filediff: { file: `${WORKTREE}/outage2.ts`, additions: 1, deletions: 0 } }, "ses-5", "call-outage-2")
check(
  "the fresh write is reported too (after the parked ones)",
  engine.reports.length === 8 && engine.reports[7]?.files_changed[0] === "outage2.ts",
  JSON.stringify(engine.reports[7]),
)
check(
  "the context warms again on the new session after the outage",
  callsTo("get_project_context").length >= 1 && engineBackUp,
)

server.closeAllConnections()
await new Promise((resolve) => server.close(() => resolve(undefined)))

console.log(`\n== RESULT: ${PASS.length} passed, ${FAIL.length} failed ==`)
if (FAIL.length) {
  console.log("FAILED:", FAIL)
  process.exit(1)
}

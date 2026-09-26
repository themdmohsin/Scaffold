/**
 * Scaffold plugin — OpenCode ⇄ Scaffold engine (Day 4).
 *
 * Day 1 logged hook fires. Day 4 wires the real loop (build plan §2 Day 4 Person A):
 *
 *   session.created ─────────► get_project_context()          (warm the cache)
 *   experimental.chat.system.transform ► inject the bounded project context block
 *   tool.execute.before (write/edit/apply_patch) ► get_api_contract(route) for any
 *        route the agent is about to write → pinned into the next request's block
 *   tool.execute.after (write/edit/apply_patch) ► report_change(diff_summary, files_changed)
 *   experimental.session.compacting ► keep the block across compaction
 *
 * Transport: MCP over streamable HTTP at `<SCAFFOLD_ENGINE_URL>/mcp` — the frozen
 * tool names in docs/API_CONTRACTS.md, called verbatim. `report_change` has no REST
 * route at all, so MCP is the only path that covers the whole loop. No SDK dependency:
 * a ~120-line JSON-RPC client so this file stays copy-pasteable into any project.
 *
 * Env (frozen in docs/API_CONTRACTS.md):
 *   SCAFFOLD_ENGINE_URL — where the Scaffold engine lives.
 *
 * Hard rules honored here:
 *   #2/#3 — the diff summary is derived deterministically from the tool result
 *           metadata (real additions/deletions), never from an LLM.
 *   #4    — only file paths + one-line summaries are ever sent; source contents,
 *           `oldString`/`newString`/`content`/`patchText` are never transmitted.
 *   #6    — the injected block is capped (MAX_CONTEXT_CHARS) and refreshed at most
 *           once per user message.
 *
 * Note for whoever edits this: the fork's `plugin.trigger` awaits hooks with no
 * error handling, so a throw inside a hook aborts the tool call. Every hook below
 * fails open (catch → log → return).
 */

import type { Plugin } from "@opencode-ai/plugin"

const ENGINE_URL = (process.env.SCAFFOLD_ENGINE_URL ?? "http://localhost:8000").replace(/\/+$/, "")
const MCP_URL = `${ENGINE_URL}/mcp`

const MCP_PROTOCOL_VERSION = "2025-06-18"
const CLIENT_INFO = { name: "scaffold-opencode-plugin", version: "0.5.0" }

/** Per-call timeout. Hooks are awaited inline, so this bounds added latency. */
const MCP_TIMEOUT_MS = 2500
/** How long a fetched context block is reused before we re-ask the engine. */
const CONTEXT_TTL_MS = 20_000
/** Repo rule #6: the always-on block stays small and targeted (~1–2K tokens). */
const MAX_CONTEXT_CHARS = 2400
const MAX_DECISIONS = 5
const MAX_CONTRACTS = 5
const MAX_ACTIVE_TASKS = 8
const MAX_PINNED_CONTRACTS = 6
const MAX_ROUTES_PER_CALL = 3
const MAX_SCHEMA_CHARS = 240
/** After repeated engine failures, stop calling for a while instead of stalling hooks. */
const FAILURE_BACKOFF_MS = 60_000
/** Parked report_change payloads kept for replay when the engine recovers. */
const MAX_RETRY_QUEUE = 50
const RETRY_MAX_AGE_MS = 60 * 60_000

/** Tools whose results mean "this session changed files". */
const WRITE_TOOLS = new Set(["write", "edit", "apply_patch"])

// ---------------------------------------------------------------------------
// Minimal MCP client (JSON-RPC 2.0 over streamable HTTP)
// ---------------------------------------------------------------------------

type JsonRpcMessage = {
  jsonrpc?: string
  id?: number | string
  result?: any
  error?: { code?: number; message?: string }
}

/** Non-2xx reply, carrying the status so callers can react (e.g. 404 = dead session). */
class HttpError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

class ScaffoldMcp {
  private readonly url: string
  private sessionId: string | null = null
  private handshake: Promise<void> | null = null
  private nextId = 1

  constructor(url: string) {
    this.url = url
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<any> {
    await this.ensureSession()
    let message: JsonRpcMessage
    try {
      message = await this.rpc("tools/call", { name, arguments: args })
    } catch (error) {
      // The engine restarted (or evicted us): its 404 says the session id is dead.
      // rpc already dropped it, so this re-handshakes and retries exactly once.
      if (!(error instanceof HttpError) || error.status !== 404) throw error
      await this.ensureSession()
      message = await this.rpc("tools/call", { name, arguments: args })
    }
    return unwrapToolResult(message)
  }

  private async ensureSession(): Promise<void> {
    if (this.sessionId) return
    if (!this.handshake) {
      this.handshake = this.startSession().finally(() => {
        this.handshake = null
      })
    }
    await this.handshake
  }

  private async startSession(): Promise<void> {
    const init = await this.rpc("initialize", {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: CLIENT_INFO,
    })
    if (!init || typeof init !== "object" || !init.serverInfo) {
      throw new Error("mcp initialize returned no serverInfo")
    }
    await this.rpc("notifications/initialized", undefined, { notify: true })
  }

  private async rpc(
    method: string,
    params: Record<string, unknown> | undefined,
    options: { notify?: boolean } = {},
  ): Promise<any> {
    const notify = options.notify === true
    const id = notify ? undefined : this.nextId++
    const body: Record<string, unknown> = { jsonrpc: "2.0", method }
    if (!notify) body.id = id
    if (params !== undefined) body.params = params

    const headers: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
    }
    // A stale session id would 404 the whole loop; initialize must go out clean.
    if (this.sessionId && method !== "initialize") headers["mcp-session-id"] = this.sessionId

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), MCP_TIMEOUT_MS)
    let res: Response
    try {
      res = await fetch(this.url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } finally {
      clearTimeout(timer)
    }

    const sessionId = res.headers.get("mcp-session-id")
    if (sessionId) this.sessionId = sessionId
    if (notify || res.status === 202 || res.status === 204) return undefined

    if (res.status === 404 && this.sessionId) {
      // Drop the session so the next attempt re-handshakes instead of retrying a dead id.
      this.sessionId = null
      throw new HttpError(`mcp ${method}: session not found (HTTP 404)`, 404)
    }

    const message = await readMessage(res, id)
    if (!message) throw new Error(`mcp ${method}: no JSON-RPC reply (HTTP ${res.status})`)
    // A 2xx body that is not a JSON-RPC reply for THIS request (an HTML error page, a
    // proxy's `{ ok: false }`, a response to someone else's request) is never a result.
    if (message.jsonrpc !== "2.0" || message.id !== id) {
      throw new Error(`mcp ${method}: not a JSON-RPC reply for request ${id} (HTTP ${res.status})`)
    }
    if (message.error) {
      const detail = message.error.message ?? JSON.stringify(message.error)
      // Drop the session so the next attempt re-handshakes instead of retrying a dead id.
      this.sessionId = null
      throw new Error(`mcp ${method}: ${detail}`)
    }
    return message.result
  }
}

/** Streamable HTTP answers with either a JSON body or an SSE stream; accept both. */
async function readMessage(res: Response, id: number | string | undefined): Promise<JsonRpcMessage | null> {
  const contentType = res.headers.get("content-type") ?? ""
  const raw = await res.text()
  if (!raw.trim()) return null
  if (contentType.includes("text/event-stream")) {
    for (const line of raw.split(/\r?\n/)) {
      if (!line.startsWith("data:")) continue
      const payload = line.slice(5).trim()
      if (!payload) continue
      const parsed = safeJson(payload)
      if (parsed && parsed.id === id) return parsed
    }
    return null
  }
  return safeJson(raw)
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** MCP tool results carry JSON in `content[0].text` (older) or `structuredContent`. */
function unwrapToolResult(result: any): any {
  const content = Array.isArray(result?.content) ? result.content : []
  const text = content.find((part: any) => part?.type === "text" && typeof part.text === "string")
  if (text) {
    const parsed = safeJson(text.text)
    return parsed === null ? text.text : parsed
  }
  if (result && typeof result === "object" && "structuredContent" in result) return result.structuredContent
  return result
}

// ---------------------------------------------------------------------------
// Deterministic extraction / rendering (no LLM anywhere in this file)
// ---------------------------------------------------------------------------

const ROUTE_RE = /["'`](\/[A-Za-z0-9][A-Za-z0-9._\-/{}:]*?)["'`]/g
const ROUTE_PREFIX_RE = /^\/(api|projects)\//
const MAX_SCAN_CHARS = 200_000
/** Args we never scan for routes: they are file paths, not endpoints. */
const NON_SCAN_KEYS = new Set(["filePath", "path", "cwd", "directory", "worktree"])

/** Pull route-looking strings ("/api/auth/login") out of the tool arguments. */
function extractRoutes(args: any): string[] {
  if (!args || typeof args !== "object") return []
  const found = new Set<string>()
  let budget = MAX_SCAN_CHARS
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (NON_SCAN_KEYS.has(key) || typeof value !== "string" || budget <= 0) continue
    const text = value.length > budget ? value.slice(0, budget) : value
    budget -= text.length
    ROUTE_RE.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = ROUTE_RE.exec(text)) !== null) {
      const route = match[1]
      if (ROUTE_PREFIX_RE.test(route)) found.add(route)
    }
  }
  return [...found]
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 24))}\n… (truncated)`
}

function relativize(file: string, root: string): string {
  const normalized = file.replaceAll("\\", "/")
  if (!root) return normalized
  const prefix = root.replaceAll("\\", "/").replace(/\/+$/, "")
  const stripped = normalized.startsWith(`${prefix}/`) ? normalized.slice(prefix.length + 1) : normalized
  return stripped.replace(/^\/+/, "")
}

function briefSchema(value: unknown): string {
  if (value === null || value === undefined) return ""
  const text = typeof value === "string" ? value : JSON.stringify(value)
  return text ? truncate(text.replace(/\s+/g, " "), MAX_SCHEMA_CHARS) : ""
}

type PinnedContract = { route: string; method: string; detail: string; at: number }

/** The bounded, always-on block injected into the system prompt (repo rule #6). */
function renderContextBlock(context: any, pins: Map<string, PinnedContract>): string {
  const lines: string[] = []
  const project = context?.project ?? {}
  const counts = context?.tasks ?? {}

  if (!context || typeof context !== "object") return ""
  lines.push("## Scaffold — live shared project context")
  lines.push(
    `Project: ${project.name ?? "unknown"}${project.goal ? ` — ${project.goal}` : ""}${
      project.deadline ? ` (deadline ${project.deadline})` : ""
    }`,
  )
  lines.push(
    `Tasks: ${counts.todo ?? 0} todo · ${counts.in_progress ?? 0} in progress · ${counts.done ?? 0} done`,
  )

  const active = Array.isArray(context.active_tasks) ? context.active_tasks.slice(0, MAX_ACTIVE_TASKS) : []
  if (active.length) {
    lines.push("Active tasks:")
    for (const task of active) {
      const due = task?.due_at ? ` (due ${task.due_at})` : ""
      lines.push(`- [${task?.status ?? "todo"}] ${task?.title ?? "untitled"}${due}`)
    }
  }

  const decisions = Array.isArray(context.recent_decisions)
    ? context.recent_decisions.slice(0, MAX_DECISIONS)
    : []
  if (decisions.length) {
    lines.push("Recent decisions — treat as settled, do not re-litigate:")
    for (const decision of decisions) lines.push(`- ${decision?.text ?? ""}`)
  }

  const contracts = Array.isArray(context.relevant_contracts)
    ? context.relevant_contracts.slice(0, MAX_CONTRACTS)
    : []
  if (contracts.length) {
    lines.push("Registered API contracts — match these exactly:")
    for (const contract of contracts) lines.push(`- ${contract?.method ?? "?"} ${contract?.route ?? "?"}`)
  }

  const pinned = [...pins.values()]
  if (pinned.length) {
    lines.push("Contracts already registered for routes in this task — do not invent a different shape:")
    for (const pin of pinned) lines.push(`- ${pin.method} ${pin.route}${pin.detail ? ` — ${pin.detail}` : ""}`)
  }

  lines.push(
    "If a change contradicts a decision or contract above, say so and log a decision instead of silently diverging.",
  )
  return truncate(lines.join("\n"), MAX_CONTEXT_CHARS)
}

type Change = { summary: string; files: string[] }
type ChangeKey = `${string}:${string}`
type QueuedChange = {
  key: ChangeKey
  payload: { diff_summary: string; files_changed: string[] }
  sessionID: string
  at: number
}

/**
 * Build the `report_change` payload from the tool result's real metadata.
 * Repo rule #4: paths + one-line summary only — never the file contents.
 */
function describeChange(tool: string, args: any, output: any, root: string): Change | null {
  const meta = output?.metadata ?? {}
  if (tool === "write") {
    const file = pickString(meta.filepath, args?.filePath)
    if (!file) return null
    const rel = relativize(file, root)
    // Real UTF-8 byte count on the wire, not JS string length (UTF-16 code units).
    const bytes = typeof args?.content === "string" ? Buffer.byteLength(args.content, "utf8") : 0
    const verb = meta.exists === true ? "updated" : "created"
    return { summary: `wrote ${rel} (${verb}, ${bytes} bytes)`, files: [rel] }
  }

  if (tool === "edit") {
    const file = pickString(meta.filediff?.file, args?.filePath, meta.filepath)
    if (!file) return null
    const rel = relativize(file, root)
    const additions = numberOrNull(meta.filediff?.additions)
    const deletions = numberOrNull(meta.filediff?.deletions)
    const delta = additions === null || deletions === null ? "" : ` (+${additions} -${deletions} lines)`
    return { summary: `edited ${rel}${delta}`, files: [rel] }
  }

  if (tool === "apply_patch") {
    const entries = Array.isArray(meta.files) ? meta.files : []
    const files: string[] = []
    const detail: string[] = []
    for (const entry of entries) {
      const raw = pickString(entry?.relativePath, entry?.filePath)
      if (!raw) continue
      const rel = relativize(raw, root)
      if (!rel || files.includes(rel)) continue
      files.push(rel)
      const additions = numberOrNull(entry?.additions) ?? 0
      const deletions = numberOrNull(entry?.deletions) ?? 0
      detail.push(`${rel} (${entry?.type ?? "update"}, +${additions} -${deletions})`)
    }
    if (!files.length) return null
    return { summary: `apply_patch: ${detail.join(", ")}`, files }
  }

  return null
}

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value
  }
  return null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

type SessionState = {
  pins: Map<string, PinnedContract>
  /** Set on every user message: the next request refetches instead of reusing. */
  stale: boolean
  reported: Set<string>
}

export const ScaffoldPlugin: Plugin = async ({ project, client, directory, worktree }) => {
  const root = worktree || directory || ""
  const mcp = new ScaffoldMcp(MCP_URL)

  let contextCache: { at: number; value: any } | null = null
  let consecutiveFailures = 0
  let cooldownUntil = 0
  /** Did the most recent engine call end in failure? Backoff alone can't tell: the
   *  first failure never trips it, yet its null is not "engine up, no data". */
  let lastEngineCallFailed = false
  const sessions = new Map<string, SessionState>()

  const log = async (level: "debug" | "info" | "warn" | "error", message: string, extra?: any) => {
    try {
      await client.app.log({ body: { service: "scaffold", level, message, ...(extra ? { extra } : {}) } })
    } catch {
      /* logging must never break a hook */
    }
  }

  const toast = async (message: string, variant: "info" | "success" | "warning" | "error") => {
    try {
      await client.tui.showToast({ body: { title: "Scaffold", message, variant } })
    } catch {
      /* non-TUI sessions have no toast surface */
    }
  }

  const sessionState = (sessionID: string): SessionState => {
    let state = sessions.get(sessionID)
    if (!state) {
      state = { pins: new Map(), stale: false, reported: new Set() }
      sessions.set(sessionID, state)
    }
    return state
  }

  /** Reports the engine failed to accept, replayed oldest-first when it recovers. */
  const retryQueue: QueuedChange[] = []

  /**
   * Replay parked report_change payloads while the engine is up. Stops at the first
   * failure so the queue keeps its order; the next hook call tries again.
   */
  const flushRetries = async (): Promise<void> => {
    while (retryQueue.length) {
      const item = retryQueue[0]
      const result = await callEngineNow("report_change", item.payload)
      if (!result?.ok) return
      retryQueue.shift()
      await log("info", `parked change reported to engine: ${item.payload.diff_summary}`, {
        files: item.payload.files_changed,
      })
    }
  }

  const engineHealthy = () => Date.now() >= cooldownUntil

  const noteFailure = async (error: unknown) => {
    consecutiveFailures += 1
    lastEngineCallFailed = true
    if (consecutiveFailures >= 2) cooldownUntil = Date.now() + FAILURE_BACKOFF_MS
    await log("warn", `engine call failed (${MCP_URL}) — continuing without Scaffold context`, {
      error: error instanceof Error ? error.message : String(error),
      consecutiveFailures,
    })
  }

  const noteSuccess = () => {
    consecutiveFailures = 0
    cooldownUntil = 0
    lastEngineCallFailed = false
  }

  /** Fail-open engine call: returns null instead of throwing into the session. */
  const callEngine = async (tool: string, args: Record<string, unknown>): Promise<any> => {
    if (!engineHealthy()) return null
    try {
      const result = await mcp.callTool(tool, args)
      noteSuccess()
      return result
    } catch (error) {
      await noteFailure(error)
      return null
    }
  }

  /** Like callEngine but ignores the backoff — replaying parked work must not be
   *  blocked by the very cooldown the outage caused. Failures still re-arm it. */
  const callEngineNow = async (tool: string, args: Record<string, unknown>): Promise<any> => {
    try {
      const result = await mcp.callTool(tool, args)
      noteSuccess()
      return result
    } catch (error) {
      await noteFailure(error)
      return null
    }
  }

  /**
   * The always-on context. Cached with a TTL and invalidated per user message, so
   * "the other dev just committed" lands on the next prompt without a fetch per hook.
   */
  const loadContext = async (sessionID: string, force = false): Promise<any> => {
    const state = sessionState(sessionID)
    const fresh = contextCache !== null && Date.now() - contextCache.at < CONTEXT_TTL_MS
    if (!force && !state.stale && fresh) return contextCache?.value ?? null
    // No project_id: the engine resolves SCAFFOLD_DEFAULT_PROJECT_ID (frozen convention).
    const value = await callEngine("get_project_context", {})
    // Only a real payload may enter the cache or clear the stale flag. Caching a failed
    // fetch (null) would pin "no context" for a whole TTL — inside the current prompt
    // and during compaction — and hide that the engine has already recovered.
    const usable = value !== null && typeof value === "object" && !value.error
    if (usable) {
      contextCache = { at: Date.now(), value }
      state.stale = false
    }
    return value
  }

  const blockFor = async (sessionID: string, force = false): Promise<string> => {
    if (!engineHealthy()) return ""
    const context = await loadContext(sessionID, force)
    if (!context || typeof context !== "object" || context.error) return ""
    return renderContextBlock(context, sessionState(sessionID).pins)
  }

  await log("info", `plugin loaded — engine=${ENGINE_URL} mcp=${MCP_URL} dir=${root || directory}`, {
    project: (project as { id?: string } | undefined)?.id ?? null,
  })

  return {
    // Session lifecycle: warm the context so the first prompt is instant, and mark
    // it stale at the end of a turn so the next prompt re-reads shared state.
    event: async (input: { event: any }) => {
      try {
        const type = input?.event?.type
        if (type === "session.created") {
          const sessionID = input.event?.properties?.sessionID ?? input.event?.properties?.info?.id
          // A new session is a natural recovery point: replay anything the engine
          // missed while it was down, before warming the context.
          await flushRetries()
          const block = sessionID ? await blockFor(String(sessionID), true) : ""
          if (block) {
            await log("info", "scaffold context attached to session", { sessionID, chars: block.length })
            await toast("project context loaded from engine", "success")
          } else if (lastEngineCallFailed || !engineHealthy()) {
            await log("warn", `engine unreachable or erroring (${consecutiveFailures} consecutive failures) — session starts without Scaffold context (engine=${MCP_URL})`)
            await toast("Scaffold engine unreachable — coding without shared context", "warning")
          } else {
            await log("warn", `engine reachable but returned no usable project context — check SCAFFOLD_DEFAULT_PROJECT_ID / seed data (engine=${MCP_URL})`)
            await toast("no project context from engine — coding without shared context", "warning")
          }
        }
        if (type === "session.idle") {
          const sessionID = input?.event?.properties?.sessionID ?? input.event?.properties?.info?.id
          if (sessionID) sessionState(String(sessionID)).stale = true
          await log("info", "hook:session.idle — agent finished; context will refresh on next prompt")
        }
      } catch (error) {
        await log("warn", "event hook failed", { error: String(error) })
      }
    },

    // A new user message means the shared state may have moved on.
    "chat.message": async (input: { sessionID: string }) => {
      try {
        if (input?.sessionID) sessionState(input.sessionID).stale = true
      } catch {
        /* ignore */
      }
    },

    // The actual injection point: the fork calls this per LLM request, before the
    // provider call, and the returned `system` array becomes system messages.
    "experimental.chat.system.transform": async (
      input: { sessionID?: string },
      output: { system: string[] },
    ) => {
      try {
        if (!input?.sessionID) return
        if (!Array.isArray(output?.system)) return
        const block = await blockFor(input.sessionID)
        if (block) output.system.push(block)
      } catch (error) {
        await log("warn", "context injection failed", { error: String(error) })
      }
    },

    // Fires before each tool call. For file-writing tools we look up the contract for
    // any route the agent is about to implement, so the next request carries the real
    // shape instead of letting the model invent one.
    "tool.execute.before": async (
      input: { tool: string; sessionID: string; callID: string },
      output: { args: any },
    ) => {
      try {
        if (!WRITE_TOOLS.has(input?.tool)) return
        const routes = extractRoutes(output?.args)
        if (!routes.length) return
        const state = sessionState(input.sessionID)
        const unknown = routes.filter((route) => !state.pins.has(route)).slice(0, MAX_ROUTES_PER_CALL)
        for (const route of unknown) {
          const contract = await callEngine("get_api_contract", { route })
          if (!contract?.found) {
            await log("debug", `no contract registered for ${route}`)
            continue
          }
          const request = briefSchema(contract.request_schema)
          const response = briefSchema(contract.response_schema)
          state.pins.set(route, {
            route: contract.route ?? route,
            method: contract.method ?? "?",
            detail: [request ? `request ${request}` : "", response ? `response ${response}` : ""]
              .filter(Boolean)
              .join(" · "),
            at: Date.now(),
          })
          await log("info", `pinned contract ${contract.method} ${contract.route} into next request`)
        }
        // Keep the injected block bounded no matter how long a session runs.
        while (state.pins.size > MAX_PINNED_CONTRACTS) {
          const oldest = [...state.pins.entries()].sort((a, b) => a[1].at - b[1].at)[0]?.[0]
          if (!oldest) break
          state.pins.delete(oldest)
        }
      } catch (error) {
        await log("warn", "tool.execute.before hook failed", { error: String(error) })
      }
    },

    // Fires after each tool call: report what actually changed back to the engine.
    "tool.execute.after": async (
      input: { tool: string; sessionID: string; callID: string; args: any },
      output: { title: string; output: string; metadata: any },
    ) => {
      try {
        if (!WRITE_TOOLS.has(input?.tool)) return
        const change = describeChange(input.tool, input.args, output, root)
        if (!change || !change.files.length) return
        const state = sessionState(input.sessionID)
        if (state.reported.has(input.callID)) return

        const result = await callEngine("report_change", {
          diff_summary: change.summary,
          files_changed: change.files,
        })
        if (result?.ok) {
          // Only successful reports are deduped, so a failed one is not silently lost.
          state.reported.add(input.callID)
          if (state.reported.size > 500) state.reported.clear()
          // The context block now understates what this session did; refresh soon.
          state.stale = true
          await log("info", `change reported to engine: ${change.summary}`, { files: change.files })
          await toast(`recorded: ${change.summary}`, "success")
        } else {
          // The write happened but the engine never saw it: park the report and
          // replay it when the engine comes back (see flushRetries).
          const key: ChangeKey = `${input.sessionID}:${input.callID}`
          if (!retryQueue.some((item) => item.key === key)) {
            retryQueue.push({
              key,
              payload: { diff_summary: change.summary, files_changed: change.files },
              sessionID: input.sessionID,
              at: Date.now(),
            })
            while (retryQueue.length > MAX_RETRY_QUEUE) retryQueue.shift()
            await log("warn", `engine did not accept report_change — parked for retry (${retryQueue.length} parked)`, {
              summary: change.summary,
            })
          }
        }
        // Retry opportunistically: every new report is a chance to drain the queue.
        await flushRetries()
        // Expire reports too old to be worth replaying; the context refresh on the
        // next prompt resyncs the agent's view anyway.
        const cutoff = Date.now() - RETRY_MAX_AGE_MS
        while (retryQueue.length && retryQueue[0].at < cutoff) retryQueue.shift()
      } catch (error) {
        await log("warn", "tool.execute.after hook failed", { error: String(error) })
      }
    },

    // Compaction rewrites the conversation; keep the shared context through it.
    "experimental.session.compacting": async (
      input: { sessionID: string },
      output: { context: string[]; prompt?: string },
    ) => {
      try {
        if (!Array.isArray(output?.context)) return
        const block = await blockFor(input.sessionID)
        if (block) output.context.push(block)
      } catch (error) {
        await log("warn", "compaction context hook failed", { error: String(error) })
      }
    },

    // Inject SCAFFOLD_* env vars into every shell execution.
    "shell.env": async (input: { cwd?: string }, output: { env: Record<string, string> }) => {
      output.env.SCAFFOLD_ENGINE_URL = ENGINE_URL
      output.env.SCAFFOLD_PROJECT_DIR = input?.cwd ?? root
    },
  }
}

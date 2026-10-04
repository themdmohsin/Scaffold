/**
 * Minimal MCP client (JSON-RPC 2.0 over streamable HTTP) for the Scaffold
 * plugin's own hook calls. The agent's tool calls go through the fork's real
 * MCP client (the engine is registered as an MCP server); this client exists
 * because hooks run before/inside tool execution and need bounded latency.
 *
 * Every call carries:
 *   Authorization: Bearer <PAT>       (frozen Phase 6 credential)
 *   X-Scaffold-Project: <uuid>        (Day 12 per-repo binding)
 */

export type ScaffoldMcpTarget = {
  url: string
  token: string
  projectId: string
}

export type JsonRpcMessage = {
  jsonrpc?: string
  id?: number | string
  result?: any
  error?: { code?: number; message?: string }
}

/** Non-2xx reply, carrying the status so callers can react (e.g. 404 = dead session). */
export class HttpError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const MCP_PROTOCOL_VERSION = "2025-06-18"
const CLIENT_INFO = { name: "scaffold-client-plugin", version: "0.6.0" }
/** Per-call timeout. Hooks are awaited inline, so this bounds added latency. */
export const MCP_TIMEOUT_MS = 2500
const MAX_SCAN_CHARS = 200_000

export class ScaffoldMcp {
  private readonly target: ScaffoldMcpTarget
  private sessionId: string | null = null
  private handshake: Promise<void> | null = null
  private nextId = 1
  private closed = false

  constructor(target: ScaffoldMcpTarget) {
    this.target = target
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<any> {
    if (this.closed) throw new Error("scaffold mcp client is closed")
    await this.ensureSession()
    let message: JsonRpcMessage
    try {
      message = await this.rpc("tools/call", { name, arguments: args })
    } catch (error) {
      // The engine restarted (or evicted us): its 404 says the session id is dead.
      if (!(error instanceof HttpError) || error.status !== 404) throw error
      await this.ensureSession()
      message = await this.rpc("tools/call", { name, arguments: args })
    }
    return unwrapToolResult(message)
  }

  /** Terminate the MCP session (plugin dispose). Best-effort, never throws. */
  async close(): Promise<void> {
    this.closed = true
    if (!this.sessionId) return
    const sessionId = this.sessionId
    this.sessionId = null
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), MCP_TIMEOUT_MS)
      try {
        await fetch(this.target.url, {
          method: "DELETE",
          headers: { ...this.headers({ method: "delete" }), "mcp-session-id": sessionId },
          signal: controller.signal,
        })
      } finally {
        clearTimeout(timer)
      }
    } catch {
      // dispose must never throw
    }
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

  private headers(options: { method: string; id?: number | string } = { method: "get" }): Record<string, string> {
    const headers: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
    }
    if (this.target.token) headers["authorization"] = `Bearer ${this.target.token}`
    if (this.target.projectId) headers["x-scaffold-project"] = this.target.projectId
    headers["x-scaffold-client"] = "scaffold-fork-plugin"
    return headers
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

    const headers = this.headers({ method })
    // A stale session id would 404 the whole loop; initialize must go out clean.
    if (this.sessionId && method !== "initialize") headers["mcp-session-id"] = this.sessionId

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), MCP_TIMEOUT_MS)
    let res: Response
    try {
      res = await fetch(this.target.url, {
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

export function safeJson(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** MCP tool results carry JSON in `content[0].text` (older) or `structuredContent`. */
export function unwrapToolResult(result: any): any {
  const content = Array.isArray(result?.content) ? result.content : []
  const text = content.find((part: any) => part?.type === "text" && typeof part.text === "string")
  if (text) {
    const parsed = safeJson(text.text)
    return parsed === null ? text.text : parsed
  }
  if (result && typeof result === "object" && "structuredContent" in result) return result.structuredContent
  return result
}

/** Cap a string; used for bounded prompt scanning. */
export function clampText(text: string, max = MAX_SCAN_CHARS): string {
  return text.length > max ? text.slice(0, max) : text
}

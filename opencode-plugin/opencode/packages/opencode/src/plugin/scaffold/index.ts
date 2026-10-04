/**
 * Scaffold plugin - built into the fork (Day 12).
 *
 * The plugin is part of the Scaffold client, not a file users copy:
 *   session.created - get_project_context()          (warm the cache)
 *   chat.message - routes in the prompt + routes in edited files are
 *                              pinned with their registered contracts BEFORE the
 *                              model writes (contract-first)
 *   experimental.chat.system.transform - inject the bounded project context block
 *   tool.execute.before - pre-write contract check (contracts/check via MCP):
 *                              a definitive conflict BLOCKS the write with the
 *                              registered contract in the error; matches/warnings
 *                              pin the registered shape for the next request
 *   tool.execute.after - report_change(diff_summary, files_changed)
 *   experimental.session.compacting - keep the block across compaction
 *   dispose - terminate the MCP session, release the duplicate guard
 *
 * Per-repo binding: `<repo>/.scaffold/project.json` (secret-free, committed);
 * the PAT lives in the OS config dir (never the repo). Every engine call carries
 * `X-Scaffold-Project`, so the engine resolves the repo's project with no global
 * SCAFFOLD_DEFAULT_PROJECT_ID dependency.
 *
 * Hard rules honored: #2/#3 routes/diffs are extracted by regex mirroring
 * diff_parser.py, never an LLM; #4 only file paths + one-line summaries leave
 * the machine (file contents are scanned locally, never sent); #6 the injected
 * block stays bounded. The dev's own provider key is never touched.
 *
 * The fork's `plugin.trigger` has no error handling, so every hook fails open
 * (catch - log - return) EXCEPT the deliberate contract block, which throws a
 * clear error so the write does not happen.
 */

import type { Plugin } from "@opencode-ai/plugin"
import { resolveScaffold, type ScaffoldRuntime } from "@opencode-ai/core/scaffold/binding"
import { ScaffoldMcp } from "./mcp"
import { extractRouteHints, extractRoutePaths, hintsFromPatch, type RouteHint } from "./contracts"

/** Guards against duplicate installs (built-in + a copied .opencode/plugins file). */
const PLUGIN_MARKER = Symbol.for("scaffold.client.plugin.instance")

/** How long a fetched context block is reused before we re-ask the engine. */
const CONTEXT_TTL_MS = 20_000
/** Repo rule #6: the always-on block stays small and targeted (~1-2K tokens). */
const MAX_CONTEXT_CHARS = 2400
const MAX_DECISIONS = 5
const MAX_CONTRACTS = 5
const MAX_ACTIVE_TASKS = 8
const MAX_PINNED_CONTRACTS = 8
const MAX_ROUTES_PER_CALL = 6
const MAX_PROMPT_ROUTES = 6
const MAX_FILES_SCANNED = 5
const MAX_FILE_BYTES = 64 * 1024
const MAX_SCHEMA_CHARS = 240
const MAX_BLOCKERS = 5
const MAX_RECENT_CHANGES = 5
/** After repeated engine failures, stop calling for a while instead of stalling hooks. */
const FAILURE_BACKOFF_MS = 60_000
/** Parked report_change payloads kept for replay when the engine recovers. */
const MAX_RETRY_QUEUE = 50
const RETRY_MAX_AGE_MS = 60 * 60_000
/** Event types worth surfacing as "what a teammate's agent just did". */
const CHANGE_EVENT_TYPES = new Set(["change_reported", "commit_ingested"])
/** Tools whose results mean "this session changed files". */
const WRITE_TOOLS = new Set(["write", "edit", "apply_patch"])

// ---------------------------------------------------------------------------
// Deterministic helpers (no LLM anywhere in this file)
// ---------------------------------------------------------------------------

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 24))}\n- (truncated)`
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

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value
  }
  return null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

/** One-line rendering of a change_reported/commit_ingested event's payload. */
function describeChangeEvent(event: any): string {
  const payload = event?.payload ?? {}
  if (event?.type === "change_reported" && typeof payload.diff_summary === "string") return payload.diff_summary
  if (event?.type === "commit_ingested") {
    const routes = Array.isArray(payload.routes_added)
      ? payload.routes_added.map((r: any) => `${r?.method ?? "?"} ${r?.route ?? "?"}`).join(", ")
      : ""
    const sha = typeof payload.sha === "string" ? payload.sha.slice(0, 7) : "?"
    return routes ? `commit ${sha}: routes ${routes}` : `commit ${sha}: ${payload.files_changed ?? 0} file(s) changed`
  }
  return event?.type ?? "change"
}

type PinnedContract = { route: string; method: string; detail: string; at: number; registered: boolean }

/** The bounded, always-on block injected into the system prompt (repo rule #6). */
function renderContextBlock(
  context: any,
  pins: Map<string, PinnedContract>,
  runtime: ScaffoldRuntime,
  offline: boolean,
): string {
  const lines: string[] = []
  const project = context?.project ?? {}
  const counts = context?.tasks ?? {}

  lines.push("## Scaffold - live shared project context")
  lines.push(
    `Project: ${project.name ?? runtime.binding.project_name ?? "unknown"}${
      project.goal ? ` - ${project.goal}` : ""
    }${project.deadline ? ` (deadline ${project.deadline})` : ""} [${runtime.projectId}]`,
  )
  if (offline) lines.push("- Engine unreachable right now - this context may be stale; Scaffold will reconnect automatically.")
  lines.push(`Tasks: ${counts.todo ?? 0} todo - ${counts.in_progress ?? 0} in progress - ${counts.done ?? 0} done`)

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
    lines.push("Recent decisions - treat as settled, do not re-litigate:")
    for (const decision of decisions) lines.push(`- ${decision?.text ?? ""}`)
  }

  const contracts = Array.isArray(context.relevant_contracts)
    ? context.relevant_contracts.slice(0, MAX_CONTRACTS)
    : []
  if (contracts.length) {
    lines.push("Registered API contracts - match these exactly:")
    for (const contract of contracts) lines.push(`- ${contract?.method ?? "?"} ${contract?.route ?? "?"}`)
  }

  const blockers = Array.isArray(context.blockers)
    ? context.blockers.filter((b: any) => !b?.resolved).slice(0, MAX_BLOCKERS)
    : []
  if (blockers.length) {
    lines.push("OPEN BLOCKERS - a teammate's change conflicts with something; resolve before continuing:")
    for (const blocker of blockers) lines.push(`- ${blocker?.description ?? ""}`)
  }

  const changes = Array.isArray(context.recent_events)
    ? context.recent_events.filter((e: any) => CHANGE_EVENT_TYPES.has(e?.type)).slice(0, MAX_RECENT_CHANGES)
    : []
  if (changes.length) {
    lines.push("Recent changes from teammates' sessions:")
    for (const change of changes) lines.push(`- ${describeChangeEvent(change)}`)
  }

  const pinned = [...pins.values()]
  if (pinned.length) {
    lines.push("Contracts already registered for routes in this task - do not invent a different shape:")
    for (const pin of pinned) lines.push(`- ${pin.method} ${pin.route}${pin.detail ? ` - ${pin.detail}` : ""}`)
  }

  lines.push(
    "The engine is also connected as MCP server `scaffold` (tools: get_active_tasks, get_ready_tasks, get_recommended_task, create_task, get_recent_decisions, get_project_environment, check_api_contracts, -) - prefer them over asking.",
  )
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
 * Repo rule #4: paths + one-line summary only - never the file contents.
 */
function describeChange(tool: string, args: any, output: any, root: string): Change | null {
  const meta = output?.metadata ?? {}
  if (tool === "write") {
    const file = pickString(meta.filepath, args?.filePath)
    if (!file) return null
    const rel = relativize(file, root)
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

/** Route hints a write tool is about to introduce (file-aware, deterministic). */
function hintsFromToolArgs(tool: string, args: any): RouteHint[] {
  if (!args || typeof args !== "object") return []
  if (tool === "write") return extractRouteHints(args.content, args.filePath)
  if (tool === "edit") return extractRouteHints(args.newString, args.filePath)
  if (tool === "apply_patch") return hintsFromPatch(args.patchText)
  return []
}

function describeCheckFinding(finding: any): string {
  const existing = finding?.existing ?? {}
  const incoming = finding?.incoming ?? {}
  const shape = [
    existing.request_schema && Object.keys(existing.request_schema).length
      ? `request ${briefSchema(existing.request_schema)}`
      : "",
    existing.response_schema && Object.keys(existing.response_schema).length
      ? `response ${briefSchema(existing.response_schema)}`
      : "",
  ]
    .filter(Boolean)
    .join(" - ")
  return `${finding?.kind ?? "conflict"}: incoming ${incoming.method ?? "?"} ${incoming.route ?? "?"} vs registered ${
    existing.method ?? "?"
  } ${existing.route ?? "?"}${shape ? ` (registered ${shape})` : ""}`
}

function checkBlockMessage(check: any): string {
  const findings = Array.isArray(check?.conflicts) ? check.conflicts : []
  const matches = Array.isArray(check?.registered_matches) ? check.registered_matches : []
  const lines = [
    "Scaffold blocked this write: it conflicts with an API contract already registered for this project.",
    "",
    ...findings.map((f: any) => `- ${describeCheckFinding(f)}`),
  ]
  for (const match of matches) {
    const registered = match?.registered ?? {}
    const shape = [
      registered.request_schema ? `request ${briefSchema(registered.request_schema)}` : "",
      registered.response_schema ? `response ${briefSchema(registered.response_schema)}` : "",
    ]
      .filter(Boolean)
      .join(" - ")
    lines.push(`- registered ${registered.method ?? "?"} ${registered.route ?? "?"}${shape ? ` (${shape})` : ""}`)
  }
  lines.push(
    "",
    "Match the registered contract exactly, or update the contract explicitly (dashboard - Contracts) before writing a different shape.",
    "If you believe this is wrong, explain why and ask the user - do not retry the same write.",
  )
  return lines.join("\n")
}

function checkWarningMessage(check: any): string {
  const matches = Array.isArray(check?.registered_matches) ? check.registered_matches : []
  const findings = Array.isArray(check?.conflicts) ? check.conflicts : []
  const parts = [
    ...findings.map((f: any) => describeCheckFinding(f)),
    ...matches.map((m: any) => `already registered: ${m?.registered?.method ?? "?"} ${m?.registered?.route ?? "?"}`),
  ]
  return truncate(parts.join("; "), 400)
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

type SessionState = {
  pins: Map<string, PinnedContract>
  /** Set on every user message: the next request refetches instead of reusing. */
  stale: boolean
  reported: Set<string>
  /** Files this session has written/edited (paths only - rule #4). */
  files: Set<string>
}

export const ScaffoldPlugin: Plugin = async ({ project, client, directory, worktree }) => {
  const root = worktree || directory || ""

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

  // --- duplicate install detection ----------------------------------------
  const globalRegistry = globalThis as Record<PropertyKey, unknown>
  const existingMarker = globalRegistry[PLUGIN_MARKER] as { source?: string } | undefined
  // A second init of the BUILT-IN plugin (instance re-creation inside the same
  // process) is not a duplicate install; only a foreign copy is.
  if (existingMarker && existingMarker.source !== "builtin") {
    await log(
      "warn",
      "duplicate Scaffold plugin detected - the built-in plugin is already active; this copy is disabled (remove .opencode/plugins/scaffold.ts)",
    )
    return {}
  }
  globalRegistry[PLUGIN_MARKER] = { root, at: Date.now(), source: "builtin" }

  // --- binding + credential (per repo, re-resolved when files change) ------
  let runtime: ScaffoldRuntime | null = resolveScaffold(root, directory)
  let mcp: ScaffoldMcp | null = null

  const refreshRuntime = (): ScaffoldRuntime | null => {
    const next = resolveScaffold(root, directory)
    if (!next) {
      runtime = null
      mcp = null
      return null
    }
    if (!runtime || runtime.engineUrl !== next.engineUrl || runtime.projectId !== next.projectId || runtime.token !== next.token) {
      void mcp?.close()
      mcp = null
    }
    runtime = next
    return next
  }

  const mcpClient = (current: ScaffoldRuntime): ScaffoldMcp => {
    if (!mcp) {
      mcp = new ScaffoldMcp({
        url: `${current.engineUrl}/mcp`,
        token: current.token,
        projectId: current.projectId,
      })
    }
    return mcp
  }

  if (runtime) {
    const auth = runtime.token ? "token" : "NO TOKEN (run `scaffold link` / set SCAFFOLD_TOKEN)"
    await log("info", `plugin loaded - project=${runtime.projectId} engine=${runtime.engineUrl} auth=${auth}`, {
      binding: runtime.binding.path,
      forkProject: (project as { id?: string } | undefined)?.id ?? null,
    })
    if (!runtime.token) {
      await log("warn", `no Scaffold credential for ${runtime.engineUrl} - hooks will run without shared context`)
    }
  } else {
    await log("info", `plugin loaded - no .scaffold/project.json under ${root || directory || "?"} (run \`scaffold init\`)`)
  }

  let contextCache: { at: number; value: any } | null = null
  let consecutiveFailures = 0
  let cooldownUntil = 0
  let lastEngineCallFailed = false
  const sessions = new Map<string, SessionState>()
  const retryQueue: QueuedChange[] = []

  const sessionState = (sessionID: string): SessionState => {
    let state = sessions.get(sessionID)
    if (!state) {
      state = { pins: new Map(), stale: false, reported: new Set(), files: new Set() }
      sessions.set(sessionID, state)
    }
    return state
  }

  const engineHealthy = () => Date.now() >= cooldownUntil

  const noteFailure = async (error: unknown) => {
    consecutiveFailures += 1
    lastEngineCallFailed = true
    if (consecutiveFailures >= 2) cooldownUntil = Date.now() + FAILURE_BACKOFF_MS
    await log("warn", `engine call failed - continuing without Scaffold context`, {
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
    const current = refreshRuntime()
    if (!current || !current.token) return null
    if (!engineHealthy()) return null
    try {
      const result = await mcpClient(current).callTool(tool, args)
      noteSuccess()
      return result
    } catch (error) {
      await noteFailure(error)
      return null
    }
  }

  /** Like callEngine but ignores the backoff - replaying parked work must not
   *  be blocked by the very cooldown the outage caused. */
  const callEngineNow = async (tool: string, args: Record<string, unknown>): Promise<any> => {
    const current = refreshRuntime()
    if (!current || !current.token) return null
    try {
      const result = await mcpClient(current).callTool(tool, args)
      noteSuccess()
      return result
    } catch (error) {
      await noteFailure(error)
      return null
    }
  }

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

  /**
   * The always-on context. Cached with a TTL and invalidated per user message,
   * so "the other dev just committed" lands on the next prompt.
   */
  const loadContext = async (sessionID: string, force = false): Promise<any> => {
    const state = sessionState(sessionID)
    const fresh = contextCache !== null && Date.now() - contextCache.at < CONTEXT_TTL_MS
    if (!force && !state.stale && fresh) return contextCache?.value ?? null
    const value = await callEngine("get_project_context", {})
    const usable = value !== null && typeof value === "object" && !value.error
    if (usable) {
      contextCache = { at: Date.now(), value }
      state.stale = false
    }
    return value
  }

  const blockFor = async (sessionID: string, force = false): Promise<string> => {
    const current = runtime
    if (!current || !engineHealthy()) return ""
    const context = await loadContext(sessionID, force)
    if (!context || typeof context !== "object" || context.error) return ""
    return renderContextBlock(context, sessionState(sessionID).pins, current, lastEngineCallFailed)
  }

  /** Pin the registered contract for a route (if any) into this session. */
  const pinRoute = async (state: SessionState, route: string): Promise<void> => {
    const contract = await callEngine("get_api_contract", { route })
    if (!contract?.found) return
    const request = briefSchema(contract.request_schema)
    const response = briefSchema(contract.response_schema)
    state.pins.set(contract.route ?? route, {
      route: contract.route ?? route,
      method: contract.method ?? "?",
      detail: [request ? `request ${request}` : "", response ? `response ${response}` : ""].filter(Boolean).join(" - "),
      at: Date.now(),
      registered: true,
    })
    await log("info", `pinned contract ${contract.method} ${contract.route} into next request`)
  }

  const trimPins = (state: SessionState) => {
    while (state.pins.size > MAX_PINNED_CONTRACTS) {
      const oldest = [...state.pins.entries()].sort((a, b) => a[1].at - b[1].at)[0]?.[0]
      if (!oldest) break
      state.pins.delete(oldest)
    }
  }

  /** Files this session edited, routes extracted LOCALLY, contracts pinned. */
  const pinRoutesFromEditedFiles = async (state: SessionState): Promise<void> => {
    const fs = await import("node:fs/promises")
    let scanned = 0
    for (const rel of state.files) {
      if (scanned >= MAX_FILES_SCANNED) break
      scanned += 1
      try {
        const abs = rel.startsWith("/") || /^[A-Za-z]:/.test(rel) ? rel : `${root.replace(/\/+$/, "")}/${rel.replace(/^\/+/, "")}`
        const stat = await fs.stat(abs)
        if (!stat.isFile() || stat.size > MAX_FILE_BYTES) continue
        const content = await fs.readFile(abs, "utf8")
        const hints = extractRouteHints(content, rel)
        for (const hint of hints.slice(0, MAX_ROUTES_PER_CALL)) {
          if (!state.pins.has(hint.route)) await pinRoute(state, hint.route)
        }
      } catch {
        /* deleted/unreadable file - skip */
      }
    }
  }

  return {
    // Session lifecycle: warm the context so the first prompt is instant.
    event: async (input: { event: any }) => {
      try {
        const type = input?.event?.type
        if (type === "session.created") {
          const sessionID = input.event?.properties?.sessionID ?? input.event?.properties?.info?.id
          await flushRetries()
          const block = sessionID ? await blockFor(String(sessionID), true) : ""
          if (block) {
            await log("info", "scaffold context attached to session", { sessionID, chars: block.length })
            await toast("project context loaded from engine", "success")
          } else if (!runtime) {
            await log("info", `no repo binding - run \`scaffold init\` to connect this repo to its shared project`, {
              dir: root || directory,
            })
          } else if (lastEngineCallFailed || !engineHealthy()) {
            await log("warn", `engine unreachable (${consecutiveFailures} consecutive failures) - session starts without Scaffold context`)
            await toast("Scaffold engine unreachable - coding without shared context", "warning")
          } else {
            await log("warn", `engine reachable but returned no usable project context`, {
              project: runtime?.projectId,
            })
            await toast("no project context from engine - coding without shared context", "warning")
          }
        }
        if (type === "session.idle") {
          const sessionID = input?.event?.properties?.sessionID ?? input?.event?.properties?.info?.id
          if (sessionID) sessionState(String(sessionID)).stale = true
        }
      } catch (error) {
        await log("warn", "event hook failed", { error: String(error) })
      }
    },

    /**
     * Contract-first: before the model writes, pin the registered contracts for
     * (a) routes mentioned in the user's prompt and (b) routes found in files
     * this session is editing. Extraction is local + deterministic; the system
     * transform then injects the pins into the very next request.
     */
    "chat.message": async (
      input: { sessionID: string },
      output: { message?: unknown; parts?: Array<{ type?: string; text?: string }> },
    ) => {
      try {
        const sessionID = input?.sessionID
        if (!sessionID) return
        const state = sessionState(sessionID)
        state.stale = true
        refreshRuntime()
        if (!runtime) return

        const text = (output?.parts ?? [])
          .filter((part) => part?.type === "text" && typeof part.text === "string")
          .map((part) => part.text as string)
          .join("\n")
        const promptRoutes = extractRoutePaths(text).slice(0, MAX_PROMPT_ROUTES)
        for (const route of promptRoutes) {
          if (!state.pins.has(route)) await pinRoute(state, route)
        }
        await pinRoutesFromEditedFiles(state)
        trimPins(state)
      } catch (error) {
        await log("warn", "chat.message hook failed", { error: String(error) })
      }
    },

    // The actual injection point: the fork calls this per LLM request.
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

    /**
     * Fires before each tool call. Two jobs:
     *  1. pin the contracts for routes the write introduces (next request);
     *  2. PRE-WRITE CHECK - ask the engine whether the write conflicts with a
     *     registered contract. A definitive conflict throws, aborting the tool
     *     with the registered contract in the message (when enforcement is on).
     */
    "tool.execute.before": async (
      input: { tool: string; sessionID: string; callID: string },
      output: { args: any },
    ) => {
      try {
        if (!WRITE_TOOLS.has(input?.tool)) return
        const state = sessionState(input.sessionID)
        const hints = hintsFromToolArgs(input.tool, output?.args)

        for (const hint of hints.slice(0, MAX_ROUTES_PER_CALL)) {
          if (!state.pins.has(hint.route)) await pinRoute(state, hint.route)
        }
        trimPins(state)

        if (!hints.length) return
        const current = refreshRuntime()
        if (!current || !current.token || !engineHealthy()) return

        const check = await callEngine("check_api_contracts", {
          routes: hints.slice(0, MAX_ROUTES_PER_CALL).map((hint) => `${hint.method} ${hint.route}`),
          file: hints.find((hint) => hint.file)?.file ?? null,
        })
        if (!check || typeof check !== "object" || check.error) return

        // Pin the registered shapes so the next request carries them.
        for (const match of Array.isArray(check.registered_matches) ? check.registered_matches : []) {
          const registered = match?.registered
          if (!registered?.route) continue
          const request = briefSchema(registered.request_schema)
          const response = briefSchema(registered.response_schema)
          state.pins.set(registered.route, {
            route: registered.route,
            method: registered.method ?? "?",
            detail: [request ? `request ${request}` : "", response ? `response ${response}` : ""]
              .filter(Boolean)
              .join(" - "),
            at: Date.now(),
            registered: true,
          })
        }
        trimPins(state)

        const blocking = check.has_blocking === true
        const advisory = (Array.isArray(check.conflicts) ? check.conflicts.length > 0 : false) || (Array.isArray(check.registered_matches) ? check.registered_matches.length > 0 : false)
        if (!blocking && !advisory) return

        if (advisory) {
          await log("warn", `contract check: ${checkWarningMessage(check)}`, {
            file: input.tool,
            paths: hints.map((hint) => `${hint.method} ${hint.route}`),
          })
          await toast(`contract check: ${checkWarningMessage(check)}`, "warning")
        }
        if (blocking) {
          if (current.binding.enforce_contracts === false) {
            await log("warn", "contract conflict detected but enforce_contracts=false - allowing the write", {
              findings: check.conflicts,
            })
            await toast("contract conflict (enforcement off) - see Scaffold logs", "warning")
            return
          }
          const message = checkBlockMessage(check)
          await log("warn", `blocked conflicting write: ${message.split("\n")[0]}`, {
            findings: check.conflicts,
            paths: hints.map((hint) => `${hint.method} ${hint.route}`),
          })
          await toast("write blocked: conflicts with a registered contract", "error")
          throw new Error(message)
        }
      } catch (error) {
        // Deliberate blocks rethrow; hook failures fail open (logged above).
        if (error instanceof Error && error.message.startsWith("Scaffold blocked this write")) throw error
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
        for (const file of change.files) state.files.add(file)
        if (state.reported.has(input.callID)) return

        const result = await callEngine("report_change", {
          diff_summary: change.summary,
          files_changed: change.files,
        })
        if (result?.ok) {
          state.reported.add(input.callID)
          if (state.reported.size > 500) state.reported.clear()
          state.stale = true
          await log("info", `change reported to engine: ${change.summary}`, { files: change.files })
          await toast(`recorded: ${change.summary}`, "success")
        } else {
          const key: ChangeKey = `${input.sessionID}:${input.callID}`
          if (!retryQueue.some((item) => item.key === key)) {
            retryQueue.push({
              key,
              payload: { diff_summary: change.summary, files_changed: change.files },
              sessionID: input.sessionID,
              at: Date.now(),
            })
            while (retryQueue.length > MAX_RETRY_QUEUE) retryQueue.shift()
            await log("warn", `engine did not accept report_change - parked for retry (${retryQueue.length} parked)`, {
              summary: change.summary,
            })
          }
        }
        await flushRetries()
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
      try {
        const current = refreshRuntime()
        if (current) {
          output.env.SCAFFOLD_ENGINE_URL = current.engineUrl
          output.env.SCAFFOLD_PROJECT_ID = current.projectId
          if (current.token) output.env.SCAFFOLD_TOKEN = current.token
        }
        output.env.SCAFFOLD_PROJECT_DIR = input?.cwd ?? root
      } catch {
        /* fail open */
      }
    },

    /**
     * Dispose: the fork calls this when the instance shuts down. Terminate the
     * MCP session and release the duplicate guard so a later instance (or a
     * reload) can install cleanly.
     */
    dispose: async () => {
      try {
        await mcp?.close()
        mcp = null
        delete globalRegistry[PLUGIN_MARKER]
        await log("info", "plugin disposed - MCP session closed")
      } catch {
        /* dispose must never throw */
      }
    },
  }
}

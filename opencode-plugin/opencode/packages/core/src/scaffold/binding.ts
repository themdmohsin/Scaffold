/**
 * Scaffold per-repo binding + credential resolution (Day 12).
 *
 * Two deliberately separate files:
 *
 *   <repo>/.scaffold/project.json   SECRET-FREE, committed with the repo.
 *       {
 *         "version": 1,
 *         "project_id": "<uuid>",
 *         "project_name": "optional display name",
 *         "engine_url": "https://engine.example.com",
 *         "dashboard_url": "https://dashboard.example.com",
 *         "enforce_contracts": true
 *       }
 *
 *   <OS config dir>/credentials.json   NEVER in the repo, chmod 600 on write.
 *       {
 *         "version": 1,
 *         "tokens": { "https://engine.example.com": "scaffold_-" }
 *       }
 *
 * The OS config dir is the fork's own config dir (`Global.Path.config`, i.e.
 * ~/.config/scaffold on Linux after the rebrand) unless overridden by
 * `SCAFFOLD_CONFIG_DIR` (additive; used by tests/CI) or `OPENCODE_CONFIG_DIR`.
 *
 * Env overrides (all frozen or documented additive names):
 *   SCAFFOLD_ENGINE_URL  overrides the binding's engine URL
 *   SCAFFOLD_TOKEN       overrides the stored PAT (CI / shells)
 *   SCAFFOLD_PROJECT_ID  overrides the binding's project id (CI / smoke runs)
 *
 * Everything here fails open: a missing/malformed file returns null, never a
 * throw - hooks and MCP registration must never break a session over config.
 */

import fs from "node:fs"
import path from "node:path"
import { Flag } from "../flag/flag"
import { Path } from "../global"

export const SCAFFOLD_DIR = ".scaffold"
export const BINDING_FILE = "project.json"
export const CREDENTIALS_FILE = "credentials.json"

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export type ScaffoldBinding = {
  version: number
  project_id: string
  project_name?: string
  engine_url: string
  dashboard_url?: string
  /** When true (default), the client may BLOCK writes that definitively
   *  conflict with a registered contract; when false it warns only. */
  enforce_contracts: boolean
  /** Absolute path the binding was read from (diagnostics only). */
  path: string
}

export type ScaffoldRuntime = {
  binding: ScaffoldBinding
  /** PAT for binding.engine_url; "" when none is configured. */
  token: string
  engineUrl: string
  projectId: string
  dashboardUrl?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** Strip trailing slashes and reject anything that is not an http(s) URL. */
export function normalizeEngineUrl(value: unknown): string | null {
  if (typeof value !== "string") return null
  const raw = value.trim().replace(/\/+$/, "")
  if (!raw) return null
  try {
    const url = new URL(raw)
    if (url.protocol !== "http:" && url.protocol !== "https:") return null
    return raw
  } catch {
    return null
  }
}

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value.trim())
}

/** Candidate binding locations, in priority order (worktree first). */
export function bindingCandidates(...directories: (string | undefined)[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const dir of directories) {
    if (!dir) continue
    const candidate = path.join(dir, SCAFFOLD_DIR, BINDING_FILE)
    if (seen.has(candidate)) continue
    seen.add(candidate)
    out.push(candidate)
  }
  return out
}

/** Parse + validate a binding object. Pure; exported for tests and the CLI. */
export function parseBinding(raw: unknown, sourcePath: string): ScaffoldBinding | null {
  if (!isRecord(raw)) return null
  const projectId = raw.project_id ?? raw.projectId
  const engineUrl = normalizeEngineUrl(raw.engine_url ?? raw.engineUrl)
  if (!isUuid(projectId) || !engineUrl) return null
  return {
    version: typeof raw.version === "number" ? raw.version : 1,
    project_id: String(projectId).trim(),
    project_name: typeof raw.project_name === "string" ? raw.project_name : undefined,
    engine_url: engineUrl,
    dashboard_url: normalizeEngineUrl(raw.dashboard_url ?? raw.dashboardUrl) ?? undefined,
    enforce_contracts: raw.enforce_contracts !== false,
    path: sourcePath,
  }
}

/** Read the repo binding; first valid candidate wins. Never throws. */
export function readBinding(...directories: (string | undefined)[]): ScaffoldBinding | null {
  for (const candidate of bindingCandidates(...directories)) {
    try {
      if (!fs.existsSync(candidate)) continue
      const parsed = parseBinding(JSON.parse(fs.readFileSync(candidate, "utf8")), candidate)
      if (parsed) return parsed
    } catch {
      // malformed JSON / unreadable file: try the next candidate
    }
  }
  return null
}

/** Write `.scaffold/project.json` (used by `scaffold init` / `scaffold link`). */
export function writeBinding(
  directory: string,
  input: {
    project_id: string
    engine_url: string
    dashboard_url?: string | null
    project_name?: string | null
    enforce_contracts?: boolean
  },
): string {
  if (!isUuid(input.project_id)) throw new Error(`project_id must be a UUID, got: ${input.project_id}`)
  const engineUrl = normalizeEngineUrl(input.engine_url)
  if (!engineUrl) throw new Error(`engine_url must be an http(s) URL, got: ${input.engine_url}`)
  const dashboardUrl = input.dashboard_url ? normalizeEngineUrl(input.dashboard_url) : null
  if (input.dashboard_url && !dashboardUrl) {
    throw new Error(`dashboard_url must be an http(s) URL, got: ${input.dashboard_url}`)
  }

  const dir = path.join(directory, SCAFFOLD_DIR)
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, BINDING_FILE)
  const body: Record<string, unknown> = {
    version: 1,
    project_id: input.project_id.trim(),
    engine_url: engineUrl,
    enforce_contracts: input.enforce_contracts === false ? false : true,
  }
  if (input.project_name) body.project_name = input.project_name
  if (dashboardUrl) body.dashboard_url = dashboardUrl
  fs.writeFileSync(file, JSON.stringify(body, null, 2) + "\n")
  return file
}

// ---------------------------------------------------------------------------
// Credentials - OS-level config dir only, never the repo
// ---------------------------------------------------------------------------

export function configDir(): string {
  const override = process.env.SCAFFOLD_CONFIG_DIR?.trim() || Flag.OPENCODE_CONFIG_DIR?.trim()
  if (override) return override
  return Path.config
}

export function credentialPath(): string {
  return path.join(configDir(), CREDENTIALS_FILE)
}

type Credentials = { version: number; tokens: Record<string, string> }

/** Read the credential file. Never throws; malformed content = no tokens. */
export function readCredentials(): Credentials {
  try {
    const file = credentialPath()
    if (!fs.existsSync(file)) return { version: 1, tokens: {} }
    const raw = JSON.parse(fs.readFileSync(file, "utf8"))
    if (!isRecord(raw) || !isRecord(raw.tokens)) return { version: 1, tokens: {} }
    const tokens: Record<string, string> = {}
    for (const [engine, token] of Object.entries(raw.tokens)) {
      const url = normalizeEngineUrl(engine)
      if (url && typeof token === "string" && token.trim()) tokens[url] = token.trim()
    }
    return { version: typeof raw.version === "number" ? raw.version : 1, tokens }
  } catch {
    return { version: 1, tokens: {} }
  }
}

/** Store/replace the PAT for one engine URL. 0600 on POSIX. Never logs values. */
export function writeToken(engineUrl: string, token: string): string {
  const url = normalizeEngineUrl(engineUrl)
  if (!url) throw new Error(`engine_url must be an http(s) URL, got: ${engineUrl}`)
  if (!token.trim()) throw new Error("refusing to store an empty token")
  const file = credentialPath()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const credentials = readCredentials()
  credentials.tokens[url] = token.trim()
  fs.writeFileSync(file, JSON.stringify(credentials, null, 2) + "\n", { mode: 0o600 })
  try {
    fs.chmodSync(file, 0o600) // existing file may have been created with broader perms
  } catch {
    // chmod is POSIX-only; Windows ACLs already restrict the user profile
  }
  return file
}

export function removeToken(engineUrl: string): boolean {
  const url = normalizeEngineUrl(engineUrl)
  if (!url) return false
  const credentials = readCredentials()
  if (!(url in credentials.tokens)) return false
  delete credentials.tokens[url]
  const file = credentialPath()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, JSON.stringify(credentials, null, 2) + "\n", { mode: 0o600 })
  return true
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

export function tokenFor(engineUrl: string | null | undefined, env: NodeJS.ProcessEnv = process.env): string {
  const fromEnv = env.SCAFFOLD_TOKEN?.trim()
  if (fromEnv) return fromEnv
  const url = normalizeEngineUrl(engineUrl)
  if (!url) return ""
  return readCredentials().tokens[url] ?? ""
}

/**
 * The one call every surface uses: repo binding + matching credential, with
 * env overrides applied. Returns null when the repo is not bound.
 */
export function resolveScaffold(
  ...directories: (string | undefined)[]
): ScaffoldRuntime | null {
  const binding = readBinding(...directories)
  if (!binding) return null
  const directory = directories.find((d): d is string => !!d) ?? ""

  const envEngine = normalizeEngineUrl(process.env.SCAFFOLD_ENGINE_URL)
  const envProject = process.env.SCAFFOLD_PROJECT_ID?.trim()
  const engineUrl = envEngine ?? binding.engine_url
  const projectId = envProject && isUuid(envProject) ? envProject : binding.project_id
  const token = tokenFor(engineUrl)

  return {
    binding,
    engineUrl,
    projectId,
    token,
    dashboardUrl: binding.dashboard_url,
  }
}

/** Human-readable one-liner for logs - never includes the token. */
export function describeRuntime(runtime: ScaffoldRuntime): string {
  return `project=${runtime.projectId} engine=${runtime.engineUrl}${runtime.token ? " auth=token" : " auth=none"}${
    runtime.binding.path ? ` binding=${runtime.binding.path}` : ""
  }`
}

export * as ScaffoldBinding from "./binding"

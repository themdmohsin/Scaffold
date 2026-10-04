/**
 * Deterministic route extraction for the Scaffold plugin (repo rule #2/#3).
 *
 * These regexes MIRROR engine/app/services/diff_parser.py - the engine is the
 * source of truth for git diffs, and the pre-write check endpoint re-validates
 * every hint with the same rules. Keeping the mirror local means only
 * route names + methods ever leave the machine; file contents never do
 * (repo rule #4).
 *
 * When diff_parser.py's patterns change, update this file in the same PR.
 */

export type RouteHint = {
  route: string
  method: string
  file?: string
}

// --- decorators / registrations (from diff_parser.py) -----------------------
const FASTAPI_RE = /@(?:app|router|api)\.(get|post|put|patch|delete|head|options)\(\s*["']([^"']+)["']/g
const API_ROUTE_RE = /@(?:app|router)\.api_route\(\s*["']([^"']+)["'].*?methods\s*=\s*\[([^\]]*)\]/g
const FLASK_RE = /@(?:app|bp|blueprint)\.route\(\s*["']([^"']+)["'](?:.*?methods\s*=\s*\[([^\]]*)\])?/gs
const EXPRESS_RE = /\b(?:app|router)\.(get|post|put|patch|delete|all)\(\s*["'`]([^"'`]+)["'`]/g

const PY_LIKE = /\.py$/
const JS_LIKE = /\.(ts|js|tsx|jsx|mjs|cjs)$/
/** Route-looking string literals in prompts / prose: "/api/..." or "/projects/...". */
const ROUTE_PATH_RE = /["'`](\/(?:api|projects)\/[A-Za-z0-9][A-Za-z0-9._\-/{}:]*?)["'`]/g

function splitMethods(raw: string | undefined): string[] {
  if (!raw) return []
  return raw
    .split(",")
    .map((m) => m.trim().replace(/^["']|["']$/g, "").toUpperCase())
    .filter(Boolean)
}

function isSourceFile(file: string | undefined): boolean {
  return !!file && (PY_LIKE.test(file) || JS_LIKE.test(file))
}

/** Extract (method, route) pairs a write is about to introduce. Pure/local. */
export function extractRouteHints(content: string | undefined, file: string | undefined): RouteHint[] {
  if (!content || !isSourceFile(file)) return []
  const found = new Map<string, RouteHint>()
  const add = (route: string, method: string) => {
    const key = `${method.toUpperCase()} ${route}`
    if (!found.has(key)) found.set(key, { route, method: method.toUpperCase(), file })
  }

  let match: RegExpExecArray | null
  API_ROUTE_RE.lastIndex = 0
  while ((match = API_ROUTE_RE.exec(content)) !== null) {
    const methods = splitMethods(match[2])
    if (methods.length === 0) add(match[1], "GET")
    for (const method of methods) add(match[1], method)
  }

  if (PY_LIKE.test(file!)) {
    FASTAPI_RE.lastIndex = 0
    while ((match = FASTAPI_RE.exec(content)) !== null) add(match[2], match[1])
    FLASK_RE.lastIndex = 0
    while ((match = FLASK_RE.exec(content)) !== null) {
      const methods = splitMethods(match[2])
      if (methods.length === 0) add(match[1], "GET")
      for (const method of methods) add(match[1], method)
    }
  }
  if (JS_LIKE.test(file!)) {
    EXPRESS_RE.lastIndex = 0
    while ((match = EXPRESS_RE.exec(content)) !== null) add(match[2], match[1] === "all" ? "GET" : match[1])
  }
  return [...found.values()]
}

/**
 * Extract route hints from an apply_patch body: per-file sections (`*** Update
 * File: <path>`) followed by added lines. Deterministic, mirrors the webhook's
 * "added lines only" rule.
 */
export function hintsFromPatch(patchText: string | undefined): RouteHint[] {
  if (!patchText) return []
  const found = new Map<string, RouteHint>()
  let file: string | undefined
  for (const line of patchText.split(/\r?\n/)) {
    const header = /^\*\*\* (?:Add|Update|Delete) File: (.+)$/.exec(line)
    if (header) {
      file = header[1].trim()
      continue
    }
    if (!line.startsWith("+")) continue
    for (const hint of extractRouteHints(line.slice(1), file)) {
      const key = `${hint.method} ${hint.route} ${hint.file ?? ""}`
      if (!found.has(key)) found.set(key, hint)
    }
  }
  return [...found.values()]
}

/** Route paths mentioned in arbitrary text (user prompt). No method inference. */
export function extractRoutePaths(text: string | undefined): string[] {
  if (!text) return []
  const found = new Set<string>()
  ROUTE_PATH_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = ROUTE_PATH_RE.exec(text)) !== null) found.add(match[1])
  return [...found]
}

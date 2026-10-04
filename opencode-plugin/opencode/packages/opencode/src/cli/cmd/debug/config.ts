import { EOL } from "os"
import { Effect } from "effect"
import { effectCmd } from "../../effect-cmd"
import { describeScaffoldMcp } from "@/mcp/scaffold"

/**
 * Redact secret-bearing values before printing the resolved config. Today this
 * covers MCP server headers (Bearer tokens, API keys) — the Scaffold engine
 * entry always carries an Authorization header when a credential is present.
 * Printed config must be safe to paste into an issue.
 */
function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact)
  if (typeof value !== "object" || value === null) return value
  const out: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (key === "headers" && typeof item === "object" && item !== null && !Array.isArray(item)) {
      const headers: Record<string, unknown> = {}
      for (const name of Object.keys(item as Record<string, unknown>)) headers[name] = "<redacted>"
      out[key] = headers
      continue
    }
    out[key] = redact(item)
  }
  return out
}

/** Where the per-repo binding was found + whether the engine is registered —
 *  never the credential itself. Null when the repo is not bound. */
function scaffoldDebugInfo(): Record<string, unknown> | null {
  return describeScaffoldMcp(process.cwd())
}

export const ConfigCommand = effectCmd({
  command: "config",
  describe: "show resolved configuration",
  builder: (yargs) => yargs,
  handler: Effect.fn("Cli.debug.config")(function* () {
    const { Config } = yield* Effect.promise(() => import("@/config/config"))
    const config = yield* Config.Service.use((cfg) => cfg.get())
    const out: Record<string, unknown> = redact(config) as Record<string, unknown>
    // The Scaffold MCP entry is derived per instance in the MCP service (so the
    // credential never lands in config files or the config HTTP API); show a
    // redacted summary here so `scaffold debug config` still proves the engine
    // is registered as an authenticated MCP server.
    const scaffold = scaffoldDebugInfo()
    if (scaffold) out.scaffold = { mcp: scaffold }
    process.stdout.write(JSON.stringify(out, null, 2) + EOL)
  }),
})

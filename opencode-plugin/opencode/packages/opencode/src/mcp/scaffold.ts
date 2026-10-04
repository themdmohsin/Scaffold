/**
 * Scaffold MCP registration (Day 12).
 *
 * When a repo is bound (`.scaffold/project.json`) and a PAT is available (OS
 * credential store or SCAFFOLD_TOKEN), the fork registers the Scaffold engine
 * as a first-class MCP server for the agent:
 *
 *   GET/POST <engine>/mcp     - streamable HTTP, Bearer PAT + X-Scaffold-Project
 *
 * Consequences:
 *   - the agent can call every engine tool (get_active_tasks, get_ready_tasks,
 *     get_recommended_task, create_task, get_recent_decisions, the environment
 *     tools, -) directly, not just the plugin's own context injection;
 *   - the TUI's MCP panel shows `scaffold` as a real server;
 *   - `opencode debug config` prints a REDACTED scaffold section (see
 *     cli/cmd/debug/index.ts) - the credential never lands in config files,
 *     the config HTTP API, or logs.
 *
 * The entry is derived in-process, per project instance: it is merged into the
 * MCP service's effective config at state creation. An explicit `mcp.scaffold`
 * entry in the user's config always wins (opt-out/override).
 */

import { resolveScaffold } from "@opencode-ai/core/scaffold/binding"
import type { ConfigMCPV1 } from "@opencode-ai/core/v1/config/mcp"

export type McpConfigMap = Record<string, ConfigMCPV1.Info>

export const SCAFFOLD_MCP_NAME = "scaffold"

export type ScaffoldMcpEntry = {
  name: string
  config: ConfigMCPV1.Info
  project: string
  engine: string
  /** Never the token itself - for logs/diagnostics. */
  authorization: "bearer" | "none"
}

/**
 * Build the derived MCP entry. Returns null when the repo is not bound or no
 * credential is available (the plugin/sidebar then explains what to do).
 */
export function scaffoldMcpEntry(directory: string | undefined): ScaffoldMcpEntry | null {
  const runtime = resolveScaffold(directory)
  if (!runtime) return null
  const config: ConfigMCPV1.Info = {
    type: "remote",
    url: `${runtime.engineUrl}/mcp`,
    enabled: true,
    // 10s default request timeout: engine tools hit Postgres; 5s (upstream
    // default) is tight for first-call cold state.
    timeout: 10_000,
    ...(runtime.token
      ? {
          headers: {
            Authorization: `Bearer ${runtime.token}`,
            "X-Scaffold-Project": runtime.projectId,
            "X-Scaffold-Client": "scaffold-fork",
          },
        }
      : {}),
  }
  return {
    name: SCAFFOLD_MCP_NAME,
    config,
    project: runtime.projectId,
    engine: runtime.engineUrl,
    authorization: runtime.token ? "bearer" : "none",
  }
}

/** Merge the derived entry into an effective MCP config map (user config wins). */
export function withScaffoldEntry(
  config: McpConfigMap | undefined,
  directory: string | undefined,
): McpConfigMap {
  const current = config ?? {}
  if (current[SCAFFOLD_MCP_NAME]) return current
  const derived = scaffoldMcpEntry(directory)
  if (!derived) return current
  return { ...current, [SCAFFOLD_MCP_NAME]: derived.config }
}



/**
 * Redacted view for `debug config`: enough to prove the registration exists,
 * never the credential.
 */
export function describeScaffoldMcp(directory: string | undefined): Record<string, unknown> | null {
  const derived = scaffoldMcpEntry(directory)
  if (!derived) return null
  return {
    name: derived.name,
    url: derived.config.type === "remote" ? derived.config.url : undefined,
    project_id: derived.project,
    authorization: derived.authorization === "bearer" ? "Authorization: Bearer <redacted PAT>" : "none",
  }
}

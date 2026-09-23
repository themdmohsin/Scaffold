/**
 * Scaffold plugin — OpenCode hook surface probe (Day 1 skeleton).
 *
 * Day 1: proves the hooks fire by structured-logging. No network calls.
 * Day 4: "before" hooks will fetch context from the engine (SCAFFOLD_ENGINE_URL),
 *        "after" hooks will report changes back via MCP report_change().
 *
 * Env (frozen in docs/API_CONTRACTS.md):
 *   SCAFFOLD_ENGINE_URL — where the Scaffold engine lives.
 *
 * Install for a project: copy this file (plus .env values) into <project>/.opencode/plugins/.
 */

import type { Plugin } from "@opencode-ai/plugin"

const ENGINE_URL = (process.env.SCAFFOLD_ENGINE_URL ?? "http://localhost:8000").replace(/\/$/, "")

export const ScaffoldPlugin: Plugin = async ({ project, client, $, directory, worktree }) => {
  await client.app.log({
    body: {
      service: "scaffold",
      level: "info",
      message: `plugin loaded — engine=${ENGINE_URL} dir=${directory} worktree=${worktree ?? "?"}`,
      extra: { project: (project as { id?: string })?.id ?? null },
    },
  })

  return {
    // Session lifecycle — Day 4: on session.start, pull get_project_context() and pin it.
    event: async ({ event }) => {
      if (event.type === "session.created") {
        await client.app.log({
          body: {
            service: "scaffold",
            level: "info",
            message: "hook:session.created — session started",
            extra: { event },
          },
        })
      }
      if (event.type === "session.idle") {
        await client.app.log({
          body: { service: "scaffold", level: "info", message: "hook:session.idle — agent finished" },
        })
      }
    },

    // Fires BEFORE each tool call. Day 4: inject project context / block divergent writes.
    "tool.execute.before": async (input, output) => {
      await client.app.log({
        body: {
          service: "scaffold",
          level: "debug",
          message: `hook:tool.execute.before tool=${input.tool}`,
          extra: { sessionID: input.sessionID, callID: input.callID },
        },
      })
    },

    // Fires AFTER each tool call. Day 4: report changes to the engine.
    "tool.execute.after": async (input, output) => {
      await client.app.log({
        body: {
          service: "scaffold",
          level: "debug",
          message: `hook:tool.execute.after tool=${input.tool}`,
          extra: { sessionID: input.sessionID, callID: input.callID, output },
        },
      })
    },

    // Inject SCAFFOLD_* env vars into every shell execution.
    "shell.env": async (input, output) => {
      output.env.SCAFFOLD_ENGINE_URL = ENGINE_URL
      output.env.SCAFFOLD_PROJECT_DIR = input.cwd ?? directory
    },
  }
}

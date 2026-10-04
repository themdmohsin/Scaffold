/**
 * `scaffold init` / `scaffold link` - per-repo project binding (Day 12).
 *
 * init: writes <repo>/.scaffold/project.json (SECRET-FREE, safe to commit) and
 *       stores the PAT in the user's OS config dir (never the repo).
 * link: changes an existing binding's project/engine/dashboard without touching
 *       credentials (useful when a project is re-created or the engine moves).
 *
 * Naming follows the frozen binding schema in docs/SCAFFOLD_BINDING.md.
 */

import type { Argv } from "yargs"
import path from "path"
import * as prompts from "@clack/prompts"
import { cmd } from "./cmd"
import { UI } from "../ui"
import {
  credentialPath,
  isUuid,
  normalizeEngineUrl,
  readBinding,
  writeBinding,
  writeToken,
} from "@opencode-ai/core/scaffold/binding"

const DEFAULT_ENGINE = "http://localhost:8000"

type Args = {
  directory?: string
  engine?: string
  project?: string
  token?: string
  dashboard?: string
  name?: string
  force?: boolean
  yes?: boolean
}

function targetDirectory(args: Args): string {
  return path.resolve(args.directory || process.cwd())
}

function interactive(): boolean {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY) && !process.env.CI
}

function fail(message: string): never {
  UI.error(message)
  process.exit(1)
}

async function askProjectId(initial?: string): Promise<string> {
  if (!interactive()) fail("--project <uuid> is required when not running interactively")
  const answer = await prompts.text({
    message: "Scaffold project id (dashboard - project - copy the UUID)",
    placeholder: initial || "00000000-0000-0000-0000-000000000000",
    validate: (value) => (isUuid(value) ? undefined : "must be a project UUID"),
  })
  if (prompts.isCancel(answer)) fail("cancelled")
  return String(answer).trim()
}

async function askToken(): Promise<string | undefined> {
  if (!interactive()) return undefined
  const answer = await prompts.password({
    message: "Personal access token (scaffold_-; stored in your OS config dir - press Enter to skip)",
  })
  if (prompts.isCancel(answer)) fail("cancelled")
  const value = String(answer).trim()
  return value || undefined
}

export const InitCommand = cmd<Args, Args>({
  command: "init [directory]",
  describe: "bind this repo to its Scaffold project (.scaffold/project.json + OS credential store)",
  builder: (yargs: Argv) =>
    yargs
      .positional("directory", { describe: "repo directory (default: current directory)", type: "string" })
      .option("engine", { describe: "Scaffold engine URL (default: $SCAFFOLD_ENGINE_URL or http://localhost:8000)", type: "string" })
      .option("project", { describe: "project id (UUID) to bind", type: "string" })
      .option("token", { describe: "personal access token (scaffold_-); stored in the OS config dir", type: "string" })
      .option("dashboard", { describe: "web dashboard URL (used by /dashboard)", type: "string" })
      .option("name", { describe: "project display name (cosmetic)", type: "string" })
      .option("force", { describe: "overwrite an existing binding", type: "boolean", default: false })
      .option("yes", { alias: "y", describe: "skip confirmation prompts", type: "boolean", default: false }),
  handler: async (args) => {
    const directory = targetDirectory(args)
    const existing = readBinding(directory)
    if (existing && !args.force) {
      UI.println(`This repo is already linked to project ${existing.project_id} (${existing.engine_url}).`)
      UI.println("Change it with `scaffold link`, or re-run with --force.")
      return
    }

    const engineUrl = normalizeEngineUrl(args.engine || process.env.SCAFFOLD_ENGINE_URL || existing?.engine_url || DEFAULT_ENGINE)
    if (!engineUrl) fail(`invalid engine URL: ${args.engine || process.env.SCAFFOLD_ENGINE_URL || DEFAULT_ENGINE}`)

    const projectId =
      (args.project || process.env.SCAFFOLD_PROJECT_ID || "").trim() || (await askProjectId(existing?.project_id))
    if (!isUuid(projectId)) fail(`project id must be a UUID, got: ${projectId}`)

    const token = (args.token || process.env.SCAFFOLD_TOKEN || "").trim() || (await askToken())

    if (!args.yes && interactive()) {
      const ok = await prompts.confirm({
        message: `Link ${directory} to project ${projectId} on ${engineUrl}?`,
        initialValue: true,
      })
      if (prompts.isCancel(ok) || ok !== true) fail("cancelled")
    }

    const file = writeBinding(directory, {
      project_id: projectId,
      engine_url: engineUrl,
      dashboard_url: args.dashboard || existing?.dashboard_url || null,
      project_name: args.name || existing?.project_name || null,
      enforce_contracts: existing ? existing.enforce_contracts : true,
    })

    let credentialNote = "no token stored - set SCAFFOLD_TOKEN or re-run with --token"
    if (token) {
      const stored = writeToken(engineUrl, token)
      credentialNote = `token stored in ${stored} (never in the repo)`
    }

    UI.println("")
    UI.println(`- Wrote ${file}`)
    UI.println(`- ${credentialNote}`)
    UI.println("")
    UI.println("Next:")
    UI.println("  1. Commit .scaffold/project.json - it is secret-free and teammates should share it.")
    UI.println("     Do NOT ignore .scaffold/; if you keep local scratch files, ignore .scaffold/*.local.json.")
    UI.println("  2. Run the client in this repo: the sidebar shows the project, and the agent")
    UI.println("     gets context + contract enforcement automatically.")
    if (!token) {
      UI.println("")
      UI.println(`Credential file (once you have a PAT): ${credentialPath()}`)
    }
  },
})

export const LinkCommand = cmd<Args, Args>({
  command: "link [directory]",
  describe: "change which Scaffold project/engine this repo is linked to (keeps the credential)",
  builder: (yargs: Argv) =>
    yargs
      .positional("directory", { describe: "repo directory (default: current directory)", type: "string" })
      .option("engine", { describe: "new engine URL", type: "string" })
      .option("project", { describe: "new project id (UUID)", type: "string" })
      .option("dashboard", { describe: "new dashboard URL", type: "string" })
      .option("name", { describe: "new project display name", type: "string" })
      .option("token", { describe: "store a new personal access token for this engine", type: "string" }),
  handler: async (args) => {
    const directory = targetDirectory(args)
    const existing = readBinding(directory)
    if (!existing) {
      fail("No .scaffold/project.json here - run `scaffold init` first.")
    }

    const projectId = (args.project || existing.project_id).trim()
    if (!isUuid(projectId)) fail(`project id must be a UUID, got: ${projectId}`)
    const engineUrl = normalizeEngineUrl(args.engine || existing.engine_url)
    if (!engineUrl) fail(`invalid engine URL: ${args.engine || existing.engine_url}`)

    const file = writeBinding(directory, {
      project_id: projectId,
      engine_url: engineUrl,
      dashboard_url: args.dashboard ?? existing.dashboard_url ?? null,
      project_name: args.name ?? existing.project_name ?? null,
      enforce_contracts: existing.enforce_contracts,
    })

    let credentialNote = "existing credential kept"
    if (args.token) {
      const stored = writeToken(engineUrl, args.token)
      credentialNote = `new token stored in ${stored}`
    }

    UI.println(`- Updated ${file}`)
    UI.println(`- ${credentialNote}`)
    UI.println(`  project ${projectId} - ${engineUrl}`)
  },
})

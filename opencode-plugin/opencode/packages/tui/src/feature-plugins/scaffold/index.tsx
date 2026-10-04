/**
 * Scaffold TUI plugin - built into the fork (Day 12).
 *
 * Surfaces the shared project inside the coding client:
 *   - a persistent Sidebar section: connection state, project, who is working
 *     on what, my recommended next task, open blockers/conflicts, last refresh;
 *   - slash commands: /scaffold (panel), /tasks, /next (accept a
 *     recommendation), /team, /conflicts, /dashboard (opens the web dashboard),
 *     /env (status only).
 *
 * Data comes from the engine's existing authenticated REST routes using the
 * per-repo binding (.scaffold/project.json + OS credential store). Offline is
 * explicit and non-fatal: the panel says so and retries on the next tick.
 * Secret values are never fetched - /env is metadata status only.
 */

import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import type { BuiltinTuiPlugin } from "../builtins"
import { createMemo, createSignal, For, Show } from "solid-js"
import open from "open"
import { resolveScaffold, type ScaffoldRuntime } from "@opencode-ai/core/scaffold/binding"

const id = "internal:scaffold"
const REFRESH_INTERVAL_MS = 30_000
const REQUEST_TIMEOUT_MS = 4000

type PanelData = {
  context: any
  coordination: any
  recommendation: any
  members: any[]
  myId: string | null
}

type Connection = "loading" | "online" | "offline" | "unbound" | "no-credential"

const time = (at: number) => new Date(at).toLocaleTimeString()

async function getJson(url: string, token: string): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: controller.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } finally {
    clearTimeout(timer)
  }
}

async function sendJson(url: string, token: string, body: unknown): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } finally {
    clearTimeout(timer)
  }
}

function runtimeOf(api: TuiPluginApi): ScaffoldRuntime | null {
  return resolveScaffold(api.state.path.worktree, api.state.path.directory)
}

function summaryLine(value: string, max = 48): string {
  return value.length > max ? `${value.slice(0, max - 1)}-` : value
}

// ---------------------------------------------------------------------------

const tui: TuiPlugin = async (api) => {
  const [connection, setConnection] = createSignal<Connection>("loading")
  const [data, setData] = createSignal<PanelData | null>(null)
  const [lastRefresh, setLastRefresh] = createSignal<number | null>(null)
  const [detail, setDetail] = createSignal<string>("")

  const theme = () => api.theme.current
  const runtime = createMemo(() => runtimeOf(api))

  const refresh = async () => {
    const current = runtimeOf(api)
    if (!current) {
      setConnection("unbound")
      setDetail("")
      return
    }
    if (!current.token) {
      setConnection("no-credential")
      setDetail(`no PAT for ${current.engineUrl} - run \`scaffold init\``)
      return
    }
    try {
      const base = `${current.engineUrl}/projects/${current.projectId}`
      const [context, coordination, members] = await Promise.all([
        getJson(`${base}/context`, current.token),
        getJson(`${base}/coordination`, current.token),
        getJson(`${base}/members`, current.token),
      ])
      const myId = Array.isArray(members) ? members.find((m: any) => m?.is_me)?.id ?? null : null
      let recommendation: any = null
      try {
        recommendation = await getJson(
          `${base}/recommendations/next${myId ? `?user_id=${encodeURIComponent(myId)}` : ""}`,
          current.token,
        )
      } catch {
        /* recommendation is optional */
      }
      setData({ context, coordination, recommendation, members: Array.isArray(members) ? members : [], myId })
      setConnection("online")
      setLastRefresh(Date.now())
      setDetail("")
    } catch (error) {
      setConnection("offline")
      setDetail(error instanceof Error ? error.message : String(error))
    }
  }
  void refresh()

  const timer = setInterval(() => void refresh(), REFRESH_INTERVAL_MS)
  api.lifecycle.onDispose(() => clearInterval(timer))

  const projectName = () => data()?.context?.project?.name ?? runtime()?.binding.project_name ?? "project"
  const working = () => {
    const rows = data()?.coordination?.who_is_doing_what
    return Array.isArray(rows) ? rows.slice(0, 3) : []
  }
  const nextTask = () => {
    const rec = data()?.recommendation?.recommendation
    if (!rec) return null
    return {
      id: rec.task?.id ?? rec.task_id ?? rec.id ?? null,
      title: rec.task?.title ?? rec.title ?? rec.action ?? null,
      reasons: Array.isArray(rec.reasons) ? rec.reasons.slice(0, 2).join(" - ") : "",
    }
  }
  const conflictCount = () => {
    const conflicts = data()?.coordination?.conflicts
    if (!conflicts) return 0
    return (
      (Array.isArray(conflicts.open_contract_conflicts) ? conflicts.open_contract_conflicts.length : 0) +
      (Array.isArray(conflicts.contract_collisions) ? conflicts.contract_collisions.length : 0)
    )
  }
  const blockerCount = () => {
    const rows = data()?.coordination?.blocked
    return Array.isArray(rows) ? rows.length : 0
  }

  // --- sidebar ------------------------------------------------------------
  api.slots.register({
    order: 150,
    slots: {
      sidebar_content() {
        const dot = () => {
          switch (connection()) {
            case "online":
              return theme().success
            case "loading":
              return theme().textMuted
            case "unbound":
              return theme().warning
            default:
              return theme().error
          }
        }
        return (
          <box>
            <box flexDirection="row" gap={1}>
              <text fg={theme().text}>
                <b>Scaffold</b>
              </text>
              <text fg={theme().textMuted}>
                {connection() === "online"
                  ? summaryLine(projectName(), 24)
                  : connection() === "loading"
                    ? "connecting-"
                    : "offline"}
              </text>
            </box>
            <box flexDirection="row" gap={1}>
              <text flexShrink={0} style={{ fg: dot() }}>
                -
              </text>
              <text fg={theme().textMuted}>
                {connection() === "online"
                  ? "engine connected"
                  : connection() === "unbound"
                    ? "not linked - run scaffold init"
                    : connection() === "no-credential"
                      ? "no credential"
                      : `engine unreachable${detail() ? ` (${detail()})` : ""}`}
              </text>
            </box>
            <Show when={connection() === "online" && data()}>
              <For each={working()}>
                {(row: any) => (
                  <text fg={theme().textMuted}>
                    {summaryLine(row?.name ?? "?", 14)} - {summaryLine(row?.open_tasks?.[0]?.title ?? "idle", 36)}
                  </text>
                )}
              </For>
              <Show when={nextTask()}>
                <text fg={theme().text}>
                  - {summaryLine(nextTask()!.title ?? "?", 44)}
                </text>
                <text fg={theme().textMuted}>your recommended next task - /next</text>
              </Show>
              <text fg={theme().textMuted}>
                {blockerCount()} blocked - {conflictCount()} conflicts
              </text>
              <text fg={theme().textMuted}>
                refreshed {lastRefresh() ? time(lastRefresh()!) : "-"} - /scaffold
              </text>
            </Show>
          </box>
        )
      },
    },
  })

  // --- dialogs -------------------------------------------------------------

  const showPanel = () => {
    const current = runtime()
    const rows: { title: string; value: string; description?: string }[] = []
    if (!current) {
      rows.push({
        title: "This repo is not linked to a Scaffold project",
        value: "unbound",
        description: "Run `scaffold init` (writes .scaffold/project.json, secret-free) and set a PAT.",
      })
    }
    if (connection() === "online") {
      rows.push({ title: `Project: ${projectName()}`, value: "project", description: current?.projectId })
      for (const row of working()) {
        rows.push({
          title: `${row?.name ?? "?"} is working on`,
          value: `who:${row?.user_id}`,
          description: summaryLine(row?.open_tasks?.[0]?.title ?? "nothing", 80),
        })
      }
      const next = nextTask()
      if (next) {
        rows.push({ title: `Next for you: ${summaryLine(next.title ?? "?", 60)}`, value: "next", description: next.reasons })
      }
      rows.push({
        title: `Conflicts: ${conflictCount()} - blocked: ${blockerCount()}`,
        value: "conflicts",
        description: "Press Enter to open /conflicts",
      })
    } else {
      rows.push({
        title: connection() === "unbound" ? "Not linked" : "Engine offline",
        value: "offline",
        description: current ? `Retrying ${current.engineUrl} every 30s - coding continues normally.` : undefined,
      })
    }
    rows.push(
      { title: "Tasks", value: "tasks", description: "-" },
      { title: "Next task", value: "next-open", description: "Recommended task + accept" },
      { title: "Team", value: "team", description: "-" },
      { title: "Conflicts", value: "conflicts", description: "-" },
      { title: "Dashboard", value: "dashboard", description: "Open the web dashboard in your browser" },
      { title: "Environment", value: "env", description: "Configuration status (never values)" },
      { title: "Refresh", value: "refresh", description: "Re-read shared state now" },
    )
    api.ui.dialog.replace(() => (
      <api.ui.DialogSelect
        title="Scaffold"
        placeholder="Choose a view"
        options={rows.map((row) => ({
          title: row.title,
          value: row.value,
          description: row.description,
          onSelect: () => {
            if (row.value === "tasks") showTasks()
            else if (row.value === "next" || row.value === "next-open") showNext()
            else if (row.value === "team") showTeam()
            else if (row.value === "conflicts") showConflicts()
            else if (row.value === "dashboard") openDashboard()
            else if (row.value === "env") showEnv()
            else if (row.value === "refresh") {
              void refresh()
              api.ui.toast({ message: "Scaffold refreshed", variant: "info" })
            }
          },
        }))}
      />
    ))
  }

  const showTasks = () => {
    const tasks = data()?.context?.active_tasks ?? []
    api.ui.dialog.replace(() => (
      <api.ui.DialogSelect
        title="Scaffold - active tasks"
        placeholder={tasks.length ? "Filter tasks" : "No active tasks"}
        options={(tasks as any[]).map((task) => ({
          title: task?.title ?? "untitled",
          value: task?.id ?? task?.title,
          description: `${task?.status ?? "todo"}${task?.due_at ? ` - due ${task?.due_at}` : ""}`,
        }))}
      />
    ))
  }

  const accept = async () => {
    const current = runtime()
    const rec = data()?.recommendation?.recommendation
    const myId = data()?.myId
    const taskId = rec?.task?.id ?? rec?.task_id ?? rec?.id
    if (!current || !taskId || !myId) {
      api.ui.toast({ message: "Nothing to accept right now", variant: "info" })
      return
    }
    try {
      await sendJson(
        `${current.engineUrl}/projects/${current.projectId}/tasks/${taskId}/accept-recommendation`,
        current.token,
        { user_id: myId },
      )
      api.ui.toast({ message: `claimed: ${summaryLine(rec?.task?.title ?? rec?.title ?? "task", 60)}`, variant: "success" })
      api.ui.dialog.clear()
      void refresh()
    } catch (error) {
      api.ui.toast({
        message: `accept failed: ${error instanceof Error ? error.message : String(error)}`,
        variant: "error",
      })
    }
  }

  const reject = async () => {
    const current = runtime()
    const rec = data()?.recommendation?.recommendation
    const myId = data()?.myId
    const taskId = rec?.task?.id ?? rec?.task_id ?? rec?.id
    if (!current || !taskId || !myId) return
    try {
      await sendJson(
        `${current.engineUrl}/projects/${current.projectId}/tasks/${taskId}/reject-recommendation`,
        current.token,
        { user_id: myId, note: "not now (client)" },
      )
      api.ui.toast({ message: "hidden for 7 days", variant: "info" })
      api.ui.dialog.clear()
      void refresh()
    } catch (error) {
      api.ui.toast({ message: `reject failed: ${String(error)}`, variant: "error" })
    }
  }

  const showNext = () => {
    const next = nextTask()
    if (!next?.id) {
      api.ui.dialog.replace(() => (
        <api.ui.DialogAlert
          title="Scaffold - next task"
          message={
            connection() === "online"
              ? "Nothing actionable right now (or the recommendation needs a project-level decision - see /scaffold)."
              : "Engine offline - start the engine or run `scaffold link` to reconnect."
          }
        />
      ))
      return
    }
    api.ui.dialog.replace(() => (
      <api.ui.DialogSelect
        title={`Scaffold - recommended for you`}
        placeholder="Choose"
        options={[
          {
            title: `Accept & claim: ${summaryLine(next.title ?? "?", 60)}`,
            value: "accept",
            description: next.reasons || "Assigns this task to you in the shared project",
            onSelect: () => void accept(),
          },
          {
            title: "Not now (hide for 7 days)",
            value: "reject",
            description: "Records the decision; nothing else changes",
            onSelect: () => void reject(),
          },
        ]}
      />
    ))
  }

  const showTeam = () => {
    const members = data()?.members ?? []
    api.ui.dialog.replace(() => (
      <api.ui.DialogSelect
        title="Scaffold - team"
        placeholder="Filter team"
        options={(members as any[]).map((member) => ({
          title: `${member?.name ?? "?"}${member?.is_me ? " (you)" : ""}`,
          value: member?.id ?? member?.name,
          description: [
            member?.kind ?? "developer",
            member?.role ?? member?.membership_role ?? "",
            member?.activity_status ?? "",
            member?.current_task?.title ? `on: ${member.current_task.title}` : "",
          ]
            .filter(Boolean)
            .join(" - "),
        }))}
      />
    ))
  }

  const showConflicts = () => {
    const conflicts = data()?.coordination?.conflicts ?? {}
    const rows: { title: string; description?: string }[] = []
    for (const blocker of conflicts.open_contract_conflicts ?? []) {
      rows.push({ title: blocker?.description ?? "open conflict", description: "contract conflict" })
    }
    for (const collision of conflicts.contract_collisions ?? []) {
      rows.push({
        title: collision?.route ? `${collision.method ?? "?"} ${collision.route}` : "contract collision",
        description: "two open tasks registered the same route",
      })
    }
    for (const overlap of conflicts.task_overlaps ?? []) {
      rows.push({ title: overlap?.a?.title ?? "task overlap", description: overlap?.b?.title ?? "potential overlap" })
    }
    for (const blocked of data()?.coordination?.blocked ?? []) {
      rows.push({
        title: blocked?.title ?? "blocked task",
        description: blocked?.manual_blocker ?? (blocked?.waiting_on?.map((w: any) => w?.title).join(", ") || "waiting"),
      })
    }
    api.ui.dialog.replace(() => (
      <api.ui.DialogSelect
        title="Scaffold - conflicts & blockers"
        placeholder={rows.length ? "Filter" : "No open conflicts or blockers -"}
        options={rows.map((row) => ({ title: row.title, value: row.title, description: row.description }))}
      />
    ))
  }

  const openDashboard = () => {
    const url = runtime()?.dashboardUrl
    if (!url) {
      api.ui.toast({
        message: "No dashboard_url in .scaffold/project.json - add one with `scaffold init` or `scaffold link`",
        variant: "warning",
      })
      return
    }
    void open(url).catch((error: unknown) =>
      api.ui.toast({ message: `could not open browser: ${String(error)}`, variant: "error" }),
    )
    api.ui.toast({ message: `opening ${url}`, variant: "info" })
  }

  const showEnv = async () => {
    const current = runtime()
    if (!current) {
      api.ui.toast({ message: "Not linked - run `scaffold init`", variant: "warning" })
      return
    }
    try {
      const status = await getJson(`${current.engineUrl}/projects/${current.projectId}/environment`, current.token)
      const variables = Array.isArray(status?.variables) ? status.variables : []
      api.ui.dialog.replace(() => (
        <api.ui.DialogSelect
          title={`Scaffold - environment (${status?.summary?.configured ?? status?.configured ?? 0}/${status?.summary?.total ?? status?.total ?? variables.length} configured)`}
          placeholder={variables.length ? "Filter keys" : "No environment variables defined"}
          options={variables.map((variable: any) => ({
            title: variable?.key ?? "?",
            value: variable?.key,
            description: [
              variable?.required ? "required" : "optional",
              variable?.is_secret === false ? "non-secret" : "secret",
              variable?.configured ? "configured" : "not configured",
              variable?.description ?? "",
            ]
              .filter(Boolean)
              .join(" - "),
          }))}
        />
      ))
    } catch (error) {
      api.ui.toast({ message: `environment status failed: ${String(error)}`, variant: "error" })
    }
  }

  // --- commands ------------------------------------------------------------
  api.keymap.registerLayer({
    commands: [
      {
        name: "scaffold.panel",
        title: "Scaffold",
        desc: "Scaffold panel - project, team, next task, conflicts",
        category: "Scaffold",
        namespace: "palette",
        slashName: "scaffold",
        run: () => showPanel(),
      },
      {
        name: "scaffold.tasks",
        title: "Scaffold tasks",
        desc: "Active tasks in the shared project",
        category: "Scaffold",
        namespace: "palette",
        slashName: "tasks",
        run: () => showTasks(),
      },
      {
        name: "scaffold.next",
        title: "Scaffold next",
        desc: "Your recommended next task (accept & claim)",
        category: "Scaffold",
        namespace: "palette",
        slashName: "next",
        run: () => showNext(),
      },
      {
        name: "scaffold.team",
        title: "Scaffold team",
        desc: "Who is on the project and what they are doing",
        category: "Scaffold",
        namespace: "palette",
        slashName: "team",
        run: () => showTeam(),
      },
      {
        name: "scaffold.conflicts",
        title: "Scaffold conflicts",
        desc: "Open contract conflicts, collisions and blockers",
        category: "Scaffold",
        namespace: "palette",
        slashName: "conflicts",
        run: () => showConflicts(),
      },
      {
        name: "scaffold.dashboard",
        title: "Scaffold dashboard",
        desc: "Open the web dashboard in your browser",
        category: "Scaffold",
        namespace: "palette",
        slashName: "dashboard",
        run: () => openDashboard(),
      },
      {
        name: "scaffold.env",
        title: "Scaffold environment",
        desc: "Environment variable status (never values)",
        category: "Scaffold",
        namespace: "palette",
        slashName: "env",
        run: () => void showEnv(),
      },
      {
        name: "scaffold.refresh",
        title: "Scaffold refresh",
        desc: "Re-read shared project state now",
        category: "Scaffold",
        namespace: "palette",
        hidden: true,
        run: () => {
          void refresh()
          api.ui.toast({ message: "Scaffold refreshed", variant: "info" })
        },
      },
    ],
  })
}

const plugin: BuiltinTuiPlugin = {
  id,
  tui,
}

export default plugin

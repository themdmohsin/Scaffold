import { useCallback, useEffect, useRef, useState } from "react";
import {
  createTask,
  addDependency,
  removeDependency,
  fetchContext,
  fetchContracts,
  fetchCoordination,
  fetchDecisions,
  fetchMembers,
  fetchTasks,
  fetchUsers,
  patchTask,
  reason,
  type ContextSummary,
  type Contract,
  type CoordinationSummary,
  type Decision,
  type MemberInfo,
  type ReasonResponse,
  type Task,
  type TaskStatus,
  type TaskUpdateBody,
  type UserInfo,
} from "./lib/api";
import { subscribeToProject, type RealtimeHandle } from "./lib/realtime";
import { timeAgo } from "./lib/format";
import Overview from "./components/Overview";
import TaskBoard from "./components/TaskBoard";
import TaskDetail from "./components/TaskDetail";
import ActiveWork from "./components/ActiveWork";
import BlockersPanel from "./components/BlockersPanel";
import ActivityFeed from "./components/ActivityFeed";
// Phase 3 (team collaboration) — isolated panel, see components/Team.tsx.
import Team from "./components/Team";
// Phase 4 (intelligent coordination) — deterministic next-actions panel.
import NextActions from "./components/NextActions";
// Phase 5 (secure environment) — configuration STATUS panel (never a value).
import Environment from "./components/Environment";

type Phase =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: ProjectData }
  | { kind: "error"; message: string };

interface ProjectData {
  context: ContextSummary;
  tasks: Task[];
  users: UserInfo[];
  decisions: Decision[];
  contracts: Contract[];
  members: MemberInfo[];
  coordination: CoordinationSummary | null;
}

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [live, setLive] = useState<string>("");
  const [busyTask, setBusyTask] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [savingTask, setSavingTask] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<{ text: string; tasks: ReasonResponse["suggested_tasks"] } | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const rtRef = useRef<RealtimeHandle | null>(null);
  const pidRef = useRef<string>("");

  const load = useCallback(async (id: string) => {
    const [context, tasks, users, decisions, contracts, members, coordination] = await Promise.all([
      fetchContext(id),
      fetchTasks(id),
      fetchUsers(id),
      fetchDecisions(id),
      fetchContracts(id),
      // Older engines (pre-Phase-3) won't have this route yet — degrade to an
      // empty roster instead of failing the whole project load.
      fetchMembers(id).catch(() => [] as MemberInfo[]),
      // Phase 4 coordination summary — deterministic, no LLM; degrade to null
      // against older engines so the panel silently hides.
      fetchCoordination(id).catch(() => null),
    ]);
    return { context, tasks, users, decisions, contracts, members, coordination };
  }, []);

  const join = useCallback(
    async (id: string) => {
      setPhase({ kind: "loading" });
      // Every mutation targets pidRef — leaving it empty made them all POST to
      // /projects//… (404) while reads still worked, so the dashboard looked
      // alive with every button dead.
      pidRef.current = id;
      try {
        const data = await load(id);
        setPhase({ kind: "ok", data });
      } catch (err) {
        setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    },
    [load],
  );

  // Supabase Realtime: refresh from the engine when any tracked table changes.
  useEffect(() => {
    if (phase.kind !== "ok") return;
    const id = pidRef.current;
    rtRef.current?.unsubscribe();
    const handle = subscribeToProject(id, () => void refresh(), (status) =>
      setLive(status === "SUBSCRIBED" ? "live" : status.toLowerCase()),
    );
    rtRef.current = handle;
    if (!handle) setLive("realtime off");
    return () => {
      handle?.unsubscribe();
      rtRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.kind]);

  async function refresh() {
    const id = pidRef.current;
    if (!id) return;
    try {
      const data = await load(id);
      setPhase({ kind: "ok", data });
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  async function moveTask(task: Task, status: TaskStatus) {
    const id = pidRef.current;
    setBusyTask(task.id);
    try {
      await patchTask(id, task.id, { status });
      await refresh();
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusyTask(null);
    }
  }

  async function createNewTask(title: string) {
    const id = pidRef.current;
    try {
      await createTask(id, { title });
      await refresh();
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  async function saveTask(taskId: string, patch: TaskUpdateBody) {
    const id = pidRef.current;
    setSavingTask(true);
    try {
      await patchTask(id, taskId, patch);
      await refresh();
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSavingTask(false);
    }
  }

  async function addDep(taskId: string, dependsOnTaskId: string) {
    const id = pidRef.current;
    try {
      await addDependency(id, taskId, dependsOnTaskId);
      await refresh();
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  async function removeDep(taskId: string, dependsOnTaskId: string) {
    const id = pidRef.current;
    try {
      await removeDependency(id, taskId, dependsOnTaskId);
      await refresh();
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  // Day 5: forward the validated owner_id/due_at from /reason, not just the title —
  // dropping them would silently un-assign the task the engine just proposed.
  async function addSuggested(task: { title: string; owner_id: string | null; due_at: string | null }) {
    const id = pidRef.current;
    try {
      await createTask(id, { title: task.title, owner_id: task.owner_id, due_at: task.due_at });
      setAnswer((a) => (a ? { ...a, tasks: a.tasks.filter((t) => t.title !== task.title) } : a));
      await refresh();
    } catch (err) {
      setAskError(err instanceof Error ? err.message : String(err));
    }
  }

  async function ask() {
    const q = prompt.trim();
    if (!q) return;
    setAsking(true);
    setAskError(null);
    try {
      const res = await reason(pidRef.current, q);
      setAnswer({ text: res.answer, tasks: res.suggested_tasks });
    } catch (err) {
      setAskError(err instanceof Error ? err.message : String(err));
    } finally {
      setAsking(false);
    }
  }

  function leave() {
    rtRef.current?.unsubscribe();
    rtRef.current = null;
    setLive("");
    setAnswer(null);
    setSelectedTaskId(null);
    pidRef.current = "";
    setPhase({ kind: "idle" });
  }

  const selectedTask = phase.kind === "ok" ? phase.data.tasks.find((t) => t.id === selectedTaskId) : undefined;

  return (
    <main className="app">
      <h1>Scaffold</h1>
      <p className="tagline">Multiple agents. Multiple workspaces. One project brain.</p>

      {phase.kind !== "ok" && (
        <div className="form">
          <input
            placeholder="Paste a project ID to join"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && join(projectId.trim())}
          />
          <button onClick={() => join(projectId.trim())} disabled={phase.kind === "loading" || !projectId.trim()}>
            {phase.kind === "loading" ? "Joining…" : "Join"}
          </button>
        </div>
      )}

      {phase.kind === "error" && <section className="status error">⚠ {phase.message}</section>}

      {phase.kind === "idle" && (
        <section className="status">Join a project to see its live control center.</section>
      )}

      {phase.kind === "ok" && (
        <>
          <header className="project-header">
            <div>
              <h2>
                {phase.data.context.project.name}{" "}
                <span className={`live live-${live === "live" ? "on" : "off"}`}>● {live || "connecting"}</span>
              </h2>
              {phase.data.context.project.goal && <p className="goal">{phase.data.context.project.goal}</p>}
            </div>
            <button onClick={leave}>Leave</button>
          </header>

          <Overview context={phase.data.context} />

          {phase.data.coordination && (
            <NextActions
              projectId={pidRef.current}
              summary={phase.data.coordination}
              members={phase.data.members}
              onChanged={refresh}
            />
          )}

          <TaskBoard
            tasks={phase.data.tasks}
            users={phase.data.users}
            busyTask={busyTask}
            onCreate={createNewTask}
            onMove={(task, status) => moveTask(task, status)}
            onSelect={setSelectedTaskId}
          />

          <ActiveWork users={phase.data.users} tasks={phase.data.tasks} recentEvents={phase.data.context.recent_events ?? []} />

          <BlockersPanel blockers={phase.data.context.blockers ?? []} tasks={phase.data.tasks} />

          <ActivityFeed events={phase.data.context.recent_events ?? []} />

          <section className="ask">
            <h3>Ask Scaffold</h3>
            <div className="ask-row">
              <textarea
                placeholder="e.g. How should we add Google login?"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={2}
              />
              <button onClick={ask} disabled={asking || !prompt.trim()}>
                {asking ? "Thinking…" : "Ask"}
              </button>
            </div>
            {askError && <p className="ask-error">⚠ {askError}</p>}
            {answer && (
              <div className="answer">
                <p>{answer.text}</p>
                {answer.tasks.length > 0 && (
                  <div className="suggested">
                    {answer.tasks.map((t) => (
                      <button key={t.title} onClick={() => addSuggested(t)}>
                        + {t.title}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          <Team
            projectId={pidRef.current}
            members={phase.data.members}
            ownerUserId={phase.data.context.project.owner_user_id ?? null}
            onChanged={refresh}
          />

          <Environment
            projectId={pidRef.current}
            ownerUserId={phase.data.context.project.owner_user_id ?? null}
            members={phase.data.members.map((m) => ({ id: m.id, name: m.name, kind: m.kind }))}
            onChanged={refresh}
          />

          <section className="panel">
            <h3>Decision log</h3>
            {phase.data.decisions.length === 0 && <p className="empty">No decisions logged yet.</p>}
            {phase.data.decisions.map((d) => (
              <div className="decision" key={d.id}>
                <p>{d.text}</p>
                {d.reasoning && <small>because: {d.reasoning}</small>}
                <small className="when">{timeAgo(d.created_at)}</small>
              </div>
            ))}
          </section>

          <section className="panel">
            <h3>API contracts</h3>
            {phase.data.contracts.length === 0 && <p className="empty">No contracts registered yet.</p>}
            <div className="contracts">
              {phase.data.contracts.map((c) => (
                <span className="contract" key={c.id}>
                  <b className={`method m-${c.method.toLowerCase()}`}>{c.method}</b> {c.route}
                </span>
              ))}
            </div>
          </section>

          {selectedTask && (
            <TaskDetail
              task={selectedTask}
              users={phase.data.users}
              allTasks={phase.data.tasks}
              saving={savingTask}
              onClose={() => setSelectedTaskId(null)}
              onSave={(patch) => saveTask(selectedTask.id, patch)}
              onAddDependency={(depId) => addDep(selectedTask.id, depId)}
              onRemoveDependency={(depId) => removeDep(selectedTask.id, depId)}
            />
          )}
        </>
      )}
    </main>
  );
}

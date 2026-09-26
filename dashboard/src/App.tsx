import { useCallback, useEffect, useRef, useState } from "react";
import {
  createTask,
  fetchContext,
  fetchContracts,
  fetchDecisions,
  fetchTasks,
  patchTask,
  reason,
  type ContextSummary,
  type Contract,
  type Decision,
  type ReasonResponse,
  type Task,
} from "./lib/api";
import { subscribeToProject, type RealtimeHandle } from "./lib/realtime";

type Phase =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: ProjectData }
  | { kind: "error"; message: string };

interface ProjectData {
  context: ContextSummary;
  tasks: Task[];
  decisions: Decision[];
  contracts: Contract[];
}

const COLUMNS: { status: Task["status"]; label: string }[] = [
  { status: "todo", label: "To do" },
  { status: "in_progress", label: "In progress" },
  { status: "done", label: "Done" },
];

function nextStatus(s: Task["status"]): Task["status"] {
  return s === "todo" ? "in_progress" : s === "in_progress" ? "done" : "todo";
}

function timeAgo(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [live, setLive] = useState<string>("");
  const [busyTask, setBusyTask] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  // Tasks reuse the API type: they carry validated owner_id/due_at (Day 5).
  const [answer, setAnswer] = useState<{ text: string; tasks: ReasonResponse["suggested_tasks"] } | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const rtRef = useRef<RealtimeHandle | null>(null);
  const pidRef = useRef<string>("");

  const load = useCallback(async (id: string) => {
    const [context, tasks, decisions, contracts] = await Promise.all([
      fetchContext(id),
      fetchTasks(id),
      fetchDecisions(id),
      fetchContracts(id),
    ]);
    return { context, tasks, decisions, contracts };
  }, []);

  const join = useCallback(
    async (id: string) => {
      setPhase({ kind: "loading" });
      // Every mutation (ask/addSuggested/addTask/moveTask) targets pidRef —
      // leaving it empty made them all POST to /projects//… (404) while reads
      // still worked, so the dashboard looked alive and every button was dead.
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
    const handle = subscribeToProject(id, () => void join(id).then(() => setPhase((p) => (p.kind === "ok" ? p : p))), (status) =>
      setLive(status === "SUBSCRIBED" ? "live" : status.toLowerCase()),
    );
    rtRef.current = handle;
    if (!handle) setLive("realtime off");
    return () => {
      handle?.unsubscribe();
      rtRef.current = null;
    };
  }, [phase.kind, join]);

  async function moveTask(task: Task) {
    const id = pidRef.current;
    setBusyTask(task.id);
    try {
      await patchTask(id, task.id, { status: nextStatus(task.status) });
      const data = await load(id);
      setPhase({ kind: "ok", data });
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusyTask(null);
    }
  }

  async function addTask() {
    const title = newTitle.trim();
    if (!title) return;
    const id = pidRef.current;
    try {
      await createTask(id, { title });
      setNewTitle("");
      const data = await load(id);
      setPhase({ kind: "ok", data });
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
      const data = await load(id);
      setPhase({ kind: "ok", data });
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
    pidRef.current = "";
    setPhase({ kind: "idle" });
  }

  return (
    <main className="app">
      <h1>Scaffold</h1>
      <p className="tagline">Shared awareness for AI coding teams.</p>

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
        <section className="status">
          Join a project to see its live task board, decision log and API contracts.
        </section>
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
              <p className="counts">
                {phase.data.context.tasks.todo} todo · {phase.data.context.tasks.in_progress} in progress ·{" "}
                {phase.data.context.tasks.done} done
              </p>
            </div>
            <button onClick={leave}>Leave</button>
          </header>

          <section className="board">
            {COLUMNS.map((col) => (
              <div className="column" key={col.status}>
                <h3>
                  {col.label} <span className="pill">{phase.data.tasks.filter((t) => t.status === col.status).length}</span>
                </h3>
                {col.status === "todo" && (
                  <div className="add-task">
                    <input
                      placeholder="New task title"
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addTask()}
                    />
                    <button onClick={addTask} disabled={!newTitle.trim()}>
                      +
                    </button>
                  </div>
                )}
                {phase.data.tasks
                  .filter((t) => t.status === col.status)
                  .map((t) => (
                    <button
                      key={t.id}
                      className={`card card-${t.status}`}
                      onClick={() => moveTask(t)}
                      disabled={busyTask === t.id}
                      title="Click to advance status"
                    >
                      <span>{t.title}</span>
                      {t.due_at && <small>due {new Date(t.due_at).toLocaleDateString()}</small>}
                    </button>
                  ))}
              </div>
            ))}
          </section>

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

          <section className="panel">
            <h3>Conflicts &amp; blockers</h3>
            {(phase.data.context.blockers?.length ?? 0) === 0 && <p className="empty">No open blockers — agents are in sync.</p>}
            {(phase.data.context.blockers ?? []).map((b) => (
              <div className="decision conflict" key={b.id}>
                <p>⚠ {b.description}</p>
                <small className="when">{timeAgo(b.created_at)}</small>
              </div>
            ))}
            {(phase.data.context.recent_events ?? []).some((e) => e.type === "conflict_flagged") && (
              <p className="empty">conflict_flagged event in the recent feed — see the blocker above.</p>
            )}
          </section>

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
        </>
      )}
    </main>
  );
}

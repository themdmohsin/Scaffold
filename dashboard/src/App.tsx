import { useState } from "react";

interface ProjectSummary {
  project: { id: string; name: string; goal: string | null; deadline: string | null };
  tasks: { todo: number; in_progress: number; done: number };
  active_tasks: { id: string; title: string; status: string }[];
  generated_at: string;
}

type Phase =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: ProjectSummary }
  | { kind: "error"; message: string };

const ENGINE_URL = (import.meta.env.VITE_ENGINE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  async function join() {
    const id = projectId.trim();
    if (!id) return;
    setPhase({ kind: "loading" });
    try {
      const res = await fetch(`${ENGINE_URL}/projects/${id}/context`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `engine returned ${res.status}`);
      }
      setPhase({ kind: "ok", data: (await res.json()) as ProjectSummary });
    } catch (err) {
      setPhase({
        kind: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <main className="app">
      <h1>Scaffold</h1>
      <p className="tagline">Shared awareness for AI coding teams.</p>

      <div className="form">
        <input
          placeholder="Paste a project ID to join"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && join()}
        />
        <button onClick={join} disabled={phase.kind === "loading"}>
          {phase.kind === "loading" ? "Joining…" : "Join"}
        </button>
      </div>

      <section className={`status ${phase.kind === "error" ? "error" : ""}`}>
        {phase.kind === "error" && <p>⚠ {phase.message}</p>}
        {phase.kind === "ok" && (
            <>
              <p>
                Joined <strong>{phase.data.project.name}</strong> — engine reachable,{" "}
                {phase.data.tasks.todo + phase.data.tasks.in_progress + phase.data.tasks.done}{" "}
                task(s) on the board.
              </p>
              <pre>{JSON.stringify(phase.data, null, 2)}</pre>
            </>
        )}
        {phase.kind === "idle" && (
          <p>
            Day 1 skeleton: joins a project via the engine and shows its live context.
            Real views land Day 3.
          </p>
        )}
      </section>
    </main>
  );
}

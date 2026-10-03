/**
 * /projects/new — the create-project wizard. Step 1: name/goal/deadline.
 * Step 2 (success): the project ID plus the exact "connect your client"
 * instructions (env vars + command) that the OpenCode plugin supports, with
 * copy buttons for each piece.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { createProject, type ProjectSummary } from "../lib/api";
import { useToast } from "../components/Toasts";
import CopyRow from "../components/CopyRow";

interface Relay {
  engineUrl: string;
  name: string;
  goal: string;
  deadline: string;
  repo: string;
}

export default function CreateProjectPage() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [deadline, setDeadline] = useState("");
  const [repo, setRepo] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createProject({
        name: name.trim(),
        goal: goal.trim() || null,
        deadline: deadline ? new Date(deadline).toISOString() : null,
        github_repo: repo.trim().replace(/^https?:\/\/github\.com\//i, "").replace(/\/+$/, "") || null,
      }),
    onSuccess: (p) => toast(`Project “${p.name}” created`, "success"),
    onError: (err) => toast(err instanceof Error ? err.message : String(err), "error"),
  });

  const created: Relay = {
    engineUrl: (import.meta.env.VITE_ENGINE_URL as string | undefined)?.trim() ?? "",
    name: name.trim(),
    goal: goal.trim(),
    deadline,
    repo: repo.trim().replace(/^https?:\/\/github\.com\//i, "").replace(/\/+$/, ""),
  };

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          <Link to="/projects">Scaffold</Link>
        </span>
      </header>
      <main className="page page-narrow">
        <div className="page-head">
          <h1>Create a project</h1>
        </div>

        {create.isSuccess ? (
          <CreatedPanel project={create.data as ProjectSummary} relay={created} onNext={() => navigate(`/projects/${create.data.id}/overview`)} />
        ) : (
          <form
            className="panel stack"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
          >
            <label htmlFor="proj-name">
              Project name <span aria-hidden="true">*</span>
            </label>
            <input
              id="proj-name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Payments revamp"
              maxLength={120}
            />
            <label htmlFor="proj-goal">Goal</label>
            <textarea
              id="proj-goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="What does success look like? This is shown to every agent before it writes code."
              rows={3}
            />
            <label htmlFor="proj-deadline">Deadline (optional)</label>
            <input id="proj-deadline" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
            <label htmlFor="proj-repo">GitHub repo (optional)</label>
            <input
              id="proj-repo"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="owner/repo — linked for reference; the webhook route is unchanged"
            />
            <small className="muted" id="repo-hint">
              The GitHub webhook integration is configured separately (see docs/DEPLOYMENT.md); this is the display link.
            </small>
            {create.isError && (
              <p className="error-text" role="alert">
                {create.error instanceof Error ? create.error.message : "Creating the project failed."}
              </p>
            )}
            <div className="row-end">
              <Link className="button ghost" to="/projects">
                Cancel
              </Link>
              <button type="submit" className="button primary" disabled={create.isPending || !name.trim()}>
                {create.isPending ? "Creating…" : "Create project"}
              </button>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}

function CreatedPanel({ project, relay, onNext }: { project: ProjectSummary; relay: Relay; onNext: () => void }) {
  return (
    <div className="stack panel">
      <h2>
        “{project.name}” is live <span className="pill">ID {project.id.slice(0, 8)}…</span>
      </h2>
      <p className="muted">
        Next: invite your team from the <strong>Team</strong> page, and optionally connect an agent client.
      </p>

      <section aria-labelledby="connect-client">
        <h3 id="connect-client">Connect your client (OpenCode plugin)</h3>
        <ol className="connect-steps">
          <li>
            <CopyRow
              label="Engine URL"
              value={relay.engineUrl || "(your deployed VITE_ENGINE_URL)"}
              note="SCAFFOLD_ENGINE_URL — where the plugin's hooks reach this project's engine"
            />
          </li>
          <li>
            <CopyRow
              label="Project ID"
              value={project.id}
              note="SCAFFOLD_PROJECT_ID — pins the plugin to this project"
            />
          </li>
          <li>
            <p>
              Create a personal access token on your projects page (Personal access tokens → Create token), then set:
            </p>
            <CopyRow label="Plugin env" value={`SCAFFOLD_TOKEN=scaffold_…`} note="The PAT from POST /auth/tokens — never commit it" />
          </li>
          <li>
            <p>Then run the client with the plugin enabled (from the plugin directory):</p>
            <CopyRow
              label="Command"
              value={'bun run --cwd packages/opencode src/index.ts run "<your prompt>"'}
              note="The fork's CLI, with .opencode/plugins/scaffold.ts present and SCAFFOLD_* above exported"
            />
          </li>
        </ol>
      </section>

      <div className="row-end">
        <Link className="button ghost" to="/projects">
          Back to projects
        </Link>
        <button type="button" className="button primary" onClick={onNext}>
          Open project →
        </button>
      </div>
    </div>
  );
}


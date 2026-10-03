/**
 * Settings — project identity and client-connection instructions: the project
 * ID, the exact "connect your client" env vars/command the plugin supports,
 * and the deadline/deadline-derived info. Read-only today (the engine's
 * frozen PATCH surface for projects is POST /projects only).
 */

import { useQuery } from "@tanstack/react-query";
import { fetchContext } from "../lib/api";
import CopyRow from "../components/CopyRow";
import { useProject } from "./ProjectLayout";
import { timeAgo } from "../lib/format";

export default function SettingsPage() {
  const { projectId, role } = useProject();
  const contextQ = useQuery({ queryKey: ["project", projectId, "context"], queryFn: ({ signal }) => fetchContext(projectId, { signal }) });
  const project = contextQ.data?.project;

  const engineUrl = (import.meta.env.VITE_ENGINE_URL as string | undefined)?.trim() || window.location.origin;

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="settings-heading">
        <h2 id="settings-heading">Project settings</h2>
        {contextQ.isLoading && <p className="muted">Loading…</p>}
        {project && (
          <dl className="settings-list">
            <dt>Name</dt>
            <dd>{project.name}</dd>
            <dt>Project ID</dt>
            <dd>
              <code>{project.id}</code>
            </dd>
            <dt>Goal</dt>
            <dd>{project.goal ?? <span className="muted">not set</span>}</dd>
            <dt>Deadline</dt>
            <dd>{project.deadline ? new Date(project.deadline).toLocaleDateString() : <span className="muted">not set</span>}</dd>
            <dt>Created</dt>
            <dd>{contextQ.data?.generated_at ? timeAgo(contextQ.data.generated_at) : ""}</dd>
            <dt>Your role</dt>
            <dd>{role}</dd>
          </dl>
        )}
      </section>

      <section className="panel" aria-labelledby="connect-heading">
        <h2 id="connect-heading">Connect a client</h2>
        <p className="muted">Every OpenCode-plugin session pointed at these values shares this project's brain.</p>
        <ol className="connect-steps">
          <li>
            <CopyRow label="Engine URL" value={engineUrl} note="SCAFFOLD_ENGINE_URL" />
          </li>
          <li>
            <CopyRow label="Project ID" value={projectId} note="SCAFFOLD_PROJECT_ID (or SCAFFOLD_DEFAULT_PROJECT_ID engine-side)" />
          </li>
          <li>
            <p className="muted">Create a personal access token (your projects page → Personal access tokens) and set SCAFFOLD_TOKEN for the plugin.</p>
          </li>
        </ol>
      </section>
    </div>
  );
}

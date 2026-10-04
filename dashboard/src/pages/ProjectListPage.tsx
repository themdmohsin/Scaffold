/**
 * /projects — the signed-in landing page. Lists the caller's projects from the
 * engine's verified GET /projects (never a pasted UUID), with create-project
 * and join-by-invite entries, plus personal access token management (shown
 * once at creation — the engine never returns the raw token again).
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createToken, fetchProjects, fetchTokens, revokeToken, type ProjectSummary, type TokenInfo } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useToast } from "../components/Toasts";
import { useConfirm } from "../components/ConfirmDialog";
import { timeAgo } from "../lib/format";

const ROLE_LABEL: Record<string, string> = { owner: "Owner", admin: "Admin", member: "Member" };

export default function ProjectListPage() {
  const { me, signOut } = useAuth();
  const qc = useQueryClient();

  const projects = useQuery({ queryKey: ["projects"], queryFn: ({ signal }) => fetchProjects({ signal }) });
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => fetchTokens() });

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Scaffold</span>
        <span className="topbar-user">
          <span className="muted">{me?.email ?? me?.full_name ?? ""}</span>
          <button type="button" className="ghost" onClick={() => void signOut()}>
            Sign out
          </button>
        </span>
      </header>
      <main className="page">
        <div className="page-head">
          <h1>Your projects</h1>
          <div className="page-head-actions">
            <Link className="button ghost" to="/join">
              Join with invite code
            </Link>
            <Link className="button primary" to="/projects/new">
              + New project
            </Link>
          </div>
        </div>

        {projects.isLoading && <p className="panel">Loading projects…</p>}
        {projects.isError && (
          <div className="panel error-panel" role="alert">
            <p>Could not load your projects: {projects.error instanceof Error ? projects.error.message : "unknown error"}</p>
            <button type="button" onClick={() => void projects.refetch()}>
              Retry
            </button>
          </div>
        )}

        {projects.data && projects.data.length === 0 && (
          <div className="panel empty-state">
            <h2>No projects yet</h2>
            <p>
              Create your first project, or redeem an invite code a teammate sent you. Scaffold gives everyone
              working on the project — human or agent — the same live picture: tasks, decisions, contracts,
              and who is doing what.
            </p>
            <div className="empty-actions">
              <Link className="button primary" to="/projects/new">
                Create a project
              </Link>
              <Link className="button ghost" to="/join">
                I have an invite code
              </Link>
            </div>
          </div>
        )}

        <ul className="project-grid">
          {projects.data?.map((p) => <ProjectCard key={p.id} project={p} />)}
        </ul>

        <PatSection
          tokens={tokens.data ?? []}
          loading={tokens.isLoading}
          onChanged={() => void qc.invalidateQueries({ queryKey: ["tokens"] })}
        />
      </main>
    </div>
  );
}

function ProjectCard({ project }: { project: ProjectSummary }) {
  return (
    <li>
      <Link to={`/projects/${project.id}/overview`} className="project-card">
        <span className="project-card-name">{project.name}</span>
        {project.goal && <span className="project-card-goal">{project.goal}</span>}
        <span className="project-card-meta">
          <span className={`role-pill role-${project.supabase_role}`}>{ROLE_LABEL[project.supabase_role] ?? project.supabase_role}</span>
          {project.deadline && <span className="muted">deadline {new Date(project.deadline).toLocaleDateString()}</span>}
          {project.github_repo && <span className="muted">⌥ {project.github_repo}</span>}
          <span className="muted">created {timeAgo(project.created_at)}</span>
        </span>
      </Link>
    </li>
  );
}

/** Personal access tokens — create (raw shown ONCE) / revoke. */
function PatSection({ tokens, loading, onChanged }: { tokens: TokenInfo[]; loading: boolean; onChanged: () => void }) {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [name, setName] = useState("");
  const [minted, setMinted] = useState<{ id: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () => createToken({ name: name.trim() || "dashboard" }),
    onSuccess: (t) => {
      setMinted({ id: t.id, token: t.token });
      setName("");
      onChanged();
    },
    onError: (err) => toast(err instanceof Error ? err.message : String(err), "error"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeToken(id),
    onSuccess: () => {
      toast("Token revoked. Anything using it stops working on its next call.", "success");
      onChanged();
    },
    onError: (err) => toast(err instanceof Error ? err.message : String(err), "error"),
  });

  return (
    <section className="panel" aria-labelledby="pat-heading">
      <h2 id="pat-heading">Personal access tokens</h2>
      <p className="muted">
        For the OpenCode plugin / CLI / MCP clients on your machine. The engine stores only a hash — the full
        token is shown once, at creation.
      </p>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label htmlFor="pat-name">Token name</label>
        <input id="pat-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="laptop" />
        <button type="submit" className="button primary" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create token"}
        </button>
      </form>

      {minted && (
        <div className="token-reveal" role="alert">
          <p>
            <strong>Copy it now — this is the only time it is shown.</strong>
          </p>
          <div className="token-row">
            <code>{minted.token}</code>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(minted.token).then(() => setCopied(true));
              }}
            >
              {copied ? "Copied ✓" : "Copy"}
            </button>
          </div>
          <button type="button" className="ghost" onClick={() => setMinted(null)}>
            Done — hide it
          </button>
        </div>
      )}

      {loading && <p className="muted">Loading tokens…</p>}
      {tokens.length > 0 && (
        <table className="data-table">
          <caption className="sr-only">Your personal access tokens</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Token</th>
              <th scope="col">Project scope</th>
              <th scope="col">Created</th>
              <th scope="col">Last used</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>
                  <code>{t.token_prefix}…</code>
                </td>
                <td>{t.project_id ? "scoped" : "all projects"}</td>
                <td>{timeAgo(t.created_at)}</td>
                <td>{t.last_used_at ? timeAgo(t.last_used_at) : "never"}</td>
                <td>
                  {t.revoked_at ? (
                    <span className="muted">revoked</span>
                  ) : (
                    <button
                      type="button"
                      className="danger-text"
                      onClick={() => {
                        void confirm({
                          title: `Revoke token “${t.name}”?`,
                          body: "The OpenCode plugin or CLI using it will start getting 401s on its next request.",
                          confirmLabel: "Revoke",
                          danger: true,
                        }).then((ok) => ok && revoke.mutate(t.id));
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

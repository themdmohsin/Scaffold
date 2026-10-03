import { useCallback, useEffect, useState } from "react";
import {
  createProject,
  createToken,
  fetchProjects,
  fetchTokens,
  joinProject,
  revokeToken,
  type Me,
  type ProjectSummary,
  type TokenInfo,
} from "../lib/api";

/**
 * Post-login landing: the projects the signed-in account is a member of
 * (GET /projects), plus create-project and join-with-invite-code. Project IDs
 * come from the server - nobody pastes a UUID. Also hosts personal access
 * tokens (what the OpenCode plugin / MCP uses as SCAFFOLD_TOKEN).
 */

interface Props {
  me: Me;
  onSelect: (projectId: string) => void;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function ProjectPicker({ me, onSelect }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [tokenName, setTokenName] = useState("");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await fetchProjects());
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  const loadTokens = useCallback(async () => {
    try {
      setTokens(await fetchTokens());
    } catch (e) {
      setTokenError(errText(e));
    }
  }, []);

  useEffect(() => {
    void loadProjects();
    void loadTokens();
  }, [loadProjects, loadTokens]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const p = await createProject({ name: name.trim(), goal: goal.trim() || null });
      onSelect(p.id);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function join() {
    setBusy(true);
    setError(null);
    try {
      const res = await joinProject({ code: code.trim() });
      onSelect(res.project_id);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function mint() {
    setTokenError(null);
    setNewToken(null);
    try {
      const t = await createToken({ name: tokenName.trim() || "laptop" });
      setNewToken(t.token);
      setTokenName("");
      void loadTokens();
    } catch (e) {
      setTokenError(errText(e));
    }
  }

  async function revoke(id: string) {
    try {
      await revokeToken(id);
      void loadTokens();
    } catch (e) {
      setTokenError(errText(e));
    }
  }

  return (
    <>
      <section className="panel">
        <h3>Your projects</h3>
        <p className="empty">
          Signed in as {me.email ?? me.full_name ?? me.account_id} ({me.memberships.length} project
          {me.memberships.length === 1 ? "" : "s"}).
        </p>
        {error && <p className="ask-error">⚠ {error}</p>}
        {projects === null && !error && <p className="empty">Loading…</p>}
        {projects?.length === 0 && (
          <p className="empty">You are not a member of any project yet - create one or join with an invite code below.</p>
        )}
        <div className="project-list">
          {projects?.map((p) => (
            <button key={p.id} className="project-card" onClick={() => onSelect(p.id)}>
              <b>{p.name}</b> <span className="badge-owner">{p.supabase_role}</span>
              {p.goal && <small className="goal">{p.goal}</small>}
            </button>
          ))}
        </div>
      </section>

      <section className="panel team-forms">
        <div className="team-form">
          <h4>Create a project</h4>
          <input placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Goal (optional)" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <button onClick={() => void create()} disabled={busy || !name.trim()}>
            Create
          </button>
        </div>
        <div className="team-form">
          <h4>Join with an invite code</h4>
          <input placeholder="Invite code" value={code} onChange={(e) => setCode(e.target.value)} />
          <button onClick={() => void join()} disabled={busy || !code.trim()}>
            Join
          </button>
        </div>
      </section>

      <section className="panel">
        <h3>Access tokens (OpenCode plugin / MCP)</h3>
        <p className="empty">
          Set a token as <code>SCAFFOLD_TOKEN</code> in the plugin environment. It is shown once and can be revoked.
        </p>
        <div className="form">
          <input placeholder="Token name (e.g. laptop)" value={tokenName} onChange={(e) => setTokenName(e.target.value)} />
          <button onClick={() => void mint()}>Create token</button>
        </div>
        {newToken && (
          <div className="invite-result">
            <code>{newToken}</code>
            <small>Copy it now - it cannot be shown again.</small>
          </div>
        )}
        {tokenError && <p className="ask-error">⚠ {tokenError}</p>}
        <table className="team-table">
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>
                  <code>{t.token_prefix}</code>
                </td>
                <td>{t.revoked_at ? "revoked" : t.last_used_at ? "used" : "unused"}</td>
                <td>{!t.revoked_at && <button onClick={() => void revoke(t.id)}>Revoke</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

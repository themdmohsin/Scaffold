import { useState } from "react";
import {
  createEnvVariable,
  fetchEnvAccess,
  fetchEnvironment,
  fetchEnvTemplate,
  grantEnvAccess,
  patchEnvVariable,
  removeEnvVariable,
  revokeEnvAccess,
  type EnvVariableInfo,
} from "../lib/api";

/**
 * Environment panel — Phase 5 (SECURE ENVIRONMENT & CONTEXT).
 *
 * Shows configuration STATUS only: which variables a project needs, whether
 * they are configured, required vs optional. There is deliberately NO way to
 * display, copy, or inspect a secret value in this UI — the engine never puts
 * one in any GET response. Values reach authorized runtimes only (agents via
 * MCP `request_environment_value`, developers via `scaffold env pull`), which
 * is exactly the Phase 5 model: metadata + permissions in the dashboard,
 * values only in the runtime.
 *
 * "Acting as" is the repo's placeholder identity (no auth yet — see Team.tsx):
 * it feeds `requesting_user_id` for owner-gated mutations.
 */

const STATUS_CLASS: Record<string, string> = {
  configured: "env-status-ok",
  required_missing: "env-status-missing",
  optional_missing: "env-status-optional",
};

interface Props {
  projectId: string;
  ownerUserId: string | null;
  members: { id: string; name: string; kind: "developer" | "agent" }[];
  onChanged: () => void;
}

export default function Environment({ projectId, ownerUserId, members, onChanged }: Props) {
  const [env, setEnv] = useState<Awaited<ReturnType<typeof fetchEnvironment>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actingAs, setActingAs] = useState("");
  const [newKey, setNewKey] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newRequired, setNewRequired] = useState(true);
  const [newValue, setNewValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [grants, setGrants] = useState<{ key: string; rows: { id: string; user_name: string | null; user_id: string }[] } | null>(null);
  const [grantUser, setGrantUser] = useState("");
  const [template, setTemplate] = useState<string | null>(null);
  // A value the user is typing into a form is local state only — it is sent to
  // the engine's secret store and never fetched back, by anyone.
  const [rotateValue, setRotateValue] = useState<Record<string, string>>({});

  async function load() {
    setError(null);
    try {
      setEnv(await fetchEnvironment(projectId));
    } catch (err) {
      // Older engines (pre-Phase-5) have no environment route — hide quietly.
      setError(err instanceof Error ? err.message : String(err));
      setEnv(null);
    }
  }

  async function loadGrants(variableId: string, key: string) {
    try {
      const rows = await fetchEnvAccess(projectId, variableId);
      setGrants({ key, rows });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addVariable() {
    if (!newKey.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createEnvVariable(projectId, {
        key: newKey.trim().toUpperCase(),
        description: newDesc.trim() || undefined,
        required: newRequired,
        value: newValue || undefined,
        created_by: actingAs || undefined,
      });
      setNewKey("");
      setNewDesc("");
      setNewValue("");
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function rotate(v: EnvVariableInfo) {
    const value = rotateValue[v.id]?.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await patchEnvVariable(projectId, v.id, {
        value,
        value_changed: true,
        requesting_user_id: actingAs || undefined,
      });
      setRotateValue((s) => ({ ...s, [v.id]: "" }));
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(v: EnvVariableInfo) {
    if (!confirm(`Remove ${v.key} (metadata, stored value and access grants)?`)) return;
    setBusy(true);
    setError(null);
    try {
      await removeEnvVariable(projectId, v.id, actingAs || undefined);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function grant(v: EnvVariableInfo) {
    if (!grantUser) return;
    setBusy(true);
    setError(null);
    try {
      await grantEnvAccess(projectId, {
        environment_variable_id: v.id,
        user_id: grantUser,
        requesting_user_id: actingAs || undefined,
      });
      await loadGrants(v.id, v.key);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(v: EnvVariableInfo, grantId: string) {
    setBusy(true);
    setError(null);
    try {
      await revokeEnvAccess(projectId, grantId, actingAs || undefined);
      await loadGrants(v.id, v.key);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function showTemplate() {
    setError(null);
    try {
      const t = await fetchEnvTemplate(projectId);
      setTemplate(t.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="panel env-panel" onLoad={undefined}>
      <h3>Environment</h3>
      <p className="env-note">
        Configuration status only — secret values are never shown here. Authorized
        agents and `scaffold env pull` retrieve values from the engine's secret store.
      </p>

      {!env && (
        <div className="env-actions">
          <button onClick={load}>Load environment status</button>
          {error && <p className="ask-error">⚠ {error}</p>}
        </div>
      )}

      {env && (
        <>
          <div className="env-acting">
            <label>
              Acting as (owner-gated actions):{" "}
              <select value={actingAs} onChange={(e) => setActingAs(e.target.value)}>
                <option value="">(none)</option>
                {members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                    {m.id === ownerUserId ? " (owner)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className="env-summary">
              {env.summary.configured}/{env.summary.total} configured
              {env.summary.required_missing > 0 && (
                <b className="env-status-missing"> · {env.summary.required_missing} required missing</b>
              )}
            </span>
            <button onClick={load} disabled={busy}>Refresh</button>
          </div>

          <table className="env-table">
            <thead>
              <tr>
                <th>Variable</th>
                <th>Status</th>
                <th>Access</th>
                <th>Rotate value</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {env.variables.map((v) => (
                <tr key={v.id} className={v.is_secret ? "env-row-secret" : undefined}>
                  <td>
                    <code>{v.key}</code>
                    {v.is_secret && <span className="badge">secret</span>}
                    {!v.is_secret && <span className="badge badge-low">non-secret</span>}
                    {v.description && <div className="env-desc">{v.description}</div>}
                  </td>
                  <td className={STATUS_CLASS[v.status] ?? ""}>
                    {v.configured ? "✓ " : "○ "}
                    {v.display_status}
                  </td>
                  <td>
                    <button
                      className="env-link"
                      disabled={busy}
                      onClick={() => loadGrants(v.id, v.key)}
                    >
                      Who has access
                    </button>
                  </td>
                  <td>
                    <div className="env-rotate">
                      <input
                        type="password"
                        placeholder={v.configured ? "new value (rotates)" : "set value"}
                        value={rotateValue[v.id] ?? ""}
                        onChange={(e) => setRotateValue((s) => ({ ...s, [v.id]: e.target.value }))}
                      />
                      <button disabled={busy || !(rotateValue[v.id] ?? "").trim()} onClick={() => rotate(v)}>
                        Set
                      </button>
                    </div>
                  </td>
                  <td className="env-row-actions">
                    <button disabled={busy} onClick={() => remove(v)}>Remove</button>
                  </td>
                </tr>
              ))}
              {env.variables.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    No environment variables defined yet — add the first below.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {grants && (
            <div className="env-grants">
              <h4>Access to <code>{grants.key}</code></h4>
              {grants.rows.length === 0 && <p className="empty">Nobody can retrieve this value yet.</p>}
              <ul>
                {grants.rows.map((g) => (
                  <li key={g.id}>
                    {g.user_name ?? g.user_id}
                    <button
                      className="env-link"
                      disabled={busy}
                      onClick={() => {
                        const v = env.variables.find((x) => x.key === grants.key);
                        if (v) revoke(v, g.id);
                      }}
                    >
                      Revoke
                    </button>
                  </li>
                ))}
              </ul>
              <div className="env-grant-form">
                <select value={grantUser} onChange={(e) => setGrantUser(e.target.value)}>
                  <option value="">Grant to…</option>
                  {members.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.kind})
                    </option>
                  ))}
                </select>
                <button
                  disabled={busy || !grantUser}
                  onClick={() => {
                    const v = env.variables.find((x) => x.key === grants.key);
                    if (v) grant(v);
                  }}
                >
                  Grant access
                </button>
              </div>
            </div>
          )}

          <div className="env-forms">
            <div className="env-form">
              <h4>Define a variable</h4>
              <input
                placeholder="KEY (SCREAMING_SNAKE_CASE)"
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
              />
              <input placeholder="Description (optional)" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
              <label className="checkbox-row">
                <input type="checkbox" checked={newRequired} onChange={(e) => setNewRequired(e.target.checked)} />
                required
              </label>
              <input
                type="password"
                placeholder="Initial value (optional — stored encrypted, never displayed)"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
              />
              <button onClick={addVariable} disabled={busy || !newKey.trim()}>
                Define variable
              </button>
            </div>

            <div className="env-form">
              <h4>.env.example template</h4>
              <button onClick={showTemplate}>Generate template</button>
              {template && <pre className="env-template">{template}</pre>}
            </div>
          </div>

          {error && <p className="ask-error">⚠ {error}</p>}
        </>
      )}
    </section>
  );
}

/**
 * Environment — configuration STATUS only (never a value). The engine's
 * Phase-5 contract guarantees no GET can carry a secret; this page adds the
 * request/grant flow views and template download. Loads automatically (the
 * old page required a manual "Load").
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEnvVariable,
  fetchEnvAccess,
  fetchEnvTemplate,
  fetchEnvironment,
  patchEnvVariable,
  removeEnvVariable,
  revokeEnvAccess,
  type EnvVariableInfo,
} from "../lib/api";
import { useProject } from "./ProjectLayout";
import { useToast } from "../components/Toasts";
import { useConfirm } from "../components/ConfirmDialog";

export default function EnvironmentPage() {
  const { projectId, role } = useProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const isOwner = role === "owner";

  const envQ = useQuery({ queryKey: ["project", projectId, "environment"], queryFn: ({ signal }) => fetchEnvironment(projectId, { signal }) });
  const [newValue, setNewValue] = useState<Record<string, string>>({});

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["project", projectId, "environment"] });

  // A value the user types is local state only — sent to the secret store in
  // the PATCH body, never fetched back by anyone.
  const rotate = useMutation({
    mutationFn: ({ v, value }: { v: EnvVariableInfo; value: string }) =>
      patchEnvVariable(projectId, v.id, { value, value_changed: true }),
    onSuccess: (_d, vars) => {
      toast(`Value set for ${vars.v.key} (stored server-side, never displayed)`, "success");
      setNewValue((s) => ({ ...s, [vars.v.id]: "" }));
      invalidate();
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Rotation failed", "error"),
  });

  const remove = useMutation({
    mutationFn: (v: EnvVariableInfo) => removeEnvVariable(projectId, v.id),
    onSuccess: (_d, v) => {
      toast(`${v.key} removed (metadata, stored value and grants)`, "success");
      invalidate();
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Remove failed", "error"),
  });

  return (
    <div className="stack">
      {envQ.isLoading && <section className="panel">Loading environment…</section>}
      {envQ.isError && (
        <div className="panel error-panel" role="alert">
          <p>Could not load environment: {envQ.error instanceof Error ? envQ.error.message : "unknown"}</p>
          <button type="button" onClick={() => void envQ.refetch()}>
            Retry
          </button>
        </div>
      )}

      {envQ.data && (
        <>
          <section className="panel" aria-labelledby="env-summary">
            <h2 id="env-summary">Configuration status</h2>
            <p className="muted">
              Status only — values are never rendered anywhere in this app. Authorized runtimes retrieve them via
              the engine's request/pull endpoints.
            </p>
            <div className="stat-row">
              <div className="stat">
                <span className="stat-value">{envQ.data.summary.total}</span>
                <span className="stat-label">Variables</span>
              </div>
              <div className="stat">
                <span className="stat-value">{envQ.data.summary.configured}</span>
                <span className="stat-label">Configured</span>
              </div>
              <div className="stat stat-warn">
                <span className="stat-value">{envQ.data.summary.required_missing}</span>
                <span className="stat-label">Required missing</span>
              </div>
            </div>
            <TemplateButton projectId={projectId} />
          </section>

          <section className="panel" aria-labelledby="env-vars">
            <h2 id="env-vars">Variables</h2>
            <div className="table-wrap">
              <table className="data-table">
                <caption className="sr-only">Environment variables and configuration status</caption>
                <thead>
                  <tr>
                    <th scope="col">Key</th>
                    <th scope="col">Status</th>
                    <th scope="col">Required</th>
                    <th scope="col">Grants</th>
                    <th scope="col">{isOwner ? "Set / rotate" : "Value"}</th>
                  </tr>
                </thead>
                <tbody>
                  {envQ.data.variables.map((v) => (
                    <tr key={v.id}>
                      <td>
                        <code>{v.key}</code>
                        {v.description && <small className="muted"> — {v.description}</small>}
                      </td>
                      <td>
                        <span className={`env-status ${v.status === "configured" ? "env-status-ok" : v.status === "required_missing" ? "env-status-missing" : "env-status-optional"}`}>
                          {v.display_status}
                        </span>
                      </td>
                      <td>{v.required ? "required" : "optional"}</td>
                      <td>
                        <GrantsCell projectId={projectId} variable={v} canGrant={isOwner} onChanged={invalidate} />
                      </td>
                      <td>
                        {isOwner ? (
                          <form
                            className="inline-form"
                            onSubmit={(e) => {
                              e.preventDefault();
                              if (newValue[v.id]) rotate.mutate({ v, value: newValue[v.id] });
                            }}
                          >
                            <label htmlFor={`envval-${v.id}`} className="sr-only">
                              New value for {v.key} (write-only)
                            </label>
                            <input
                              id={`envval-${v.id}`}
                              type="password"
                              autoComplete="off"
                              placeholder={v.configured ? "rotate value" : "set value"}
                              value={newValue[v.id] ?? ""}
                              onChange={(e) => setNewValue((s) => ({ ...s, [v.id]: e.target.value }))}
                            />
                            <button type="submit" disabled={!newValue[v.id] || rotate.isPending}>
                              Save
                            </button>
                            <button
                              type="button"
                              className="danger-text"
                              onClick={() =>
                                void confirm({
                                  title: `Remove ${v.key}?`,
                                  body: "The metadata, the stored value and every access grant go with it.",
                                  confirmLabel: "Remove",
                                  danger: true,
                                }).then((ok) => ok && remove.mutate(v))
                              }
                            >
                              Remove
                            </button>
                          </form>
                        ) : (
                          <span className="muted">write-only (owner manages)</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {isOwner && <DefineVariable projectId={projectId} onDone={invalidate} />}
        </>
      )}
    </div>
  );
}

function GrantsCell({
  projectId,
  variable,
  canGrant,
  onChanged,
}: {
  projectId: string;
  variable: EnvVariableInfo;
  canGrant: boolean;
  onChanged: () => void;
}) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const grantsQ = useQuery({
    queryKey: ["project", projectId, "envgrants", variable.id],
    queryFn: ({ signal }) => fetchEnvAccess(projectId, variable.id, { signal }),
    enabled: open,
  });
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeEnvAccess(projectId, grantId),
    onSuccess: () => {
      toast("Grant revoked", "success");
      onChanged();
      void grantsQ.refetch();
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Revoke failed", "error"),
  });

  return (
    <div>
      <button type="button" className="linklike" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "View"} grants
      </button>
      {open && (
        <ul className="dep-list">
          {(grantsQ.data ?? []).map((g) => (
            <li key={g.id}>
              {g.user_name ?? g.user_id}
              {canGrant && (
                <button type="button" className="dep-remove" onClick={() => revoke.mutate(g.id)}>
                  revoke
                </button>
              )}
            </li>
          ))}
          {(grantsQ.data ?? []).length === 0 && <li className="muted">No grants — nobody can retrieve this value.</li>}
        </ul>
      )}
    </div>
  );
}

function DefineVariable({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const { toast } = useToast();
  const [key, setKey] = useState("");
  const [desc, setDesc] = useState("");
  const [required, setRequired] = useState(true);
  const [isSecret, setIsSecret] = useState(true);
  const [value, setValue] = useState("");

  const define = useMutation({
    mutationFn: () =>
      createEnvVariable(projectId, {
        key: key.trim().toUpperCase(),
        description: desc.trim() || undefined,
        required,
        is_secret: isSecret,
        value: value || undefined,
      }),
    onSuccess: () => {
      toast("Variable defined", "success");
      setKey("");
      setDesc("");
      setValue("");
      onDone();
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Define failed", "error"),
  });

  return (
    <section className="panel" aria-labelledby="define-var">
      <h2 id="define-var">Define a variable</h2>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (key.trim()) define.mutate();
        }}
      >
        <div className="inline-form">
          <label htmlFor="env-key">Key</label>
          <input
            id="env-key"
            required
            value={key}
            onChange={(e) => setKey(e.target.value.toUpperCase())}
            placeholder="DATABASE_URL"
            pattern="[A-Z][A-Z0-9_]*"
          />
          <label htmlFor="env-desc">Description</label>
          <input id="env-desc" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Postgres connection string" />
        </div>
        <div className="inline-form">
          <label className="checkbox-row">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            Required
          </label>
          <label className="checkbox-row">
            <input type="checkbox" checked={isSecret} onChange={(e) => setIsSecret(e.target.checked)} />
            Secret
          </label>
          <label htmlFor="env-init" className="sr-only">
            Initial value (write-only)
          </label>
          <input
            id="env-init"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="initial value (optional, write-only)"
          />
          <button type="submit" className="button primary" disabled={define.isPending}>
            Define
          </button>
        </div>
      </form>
    </section>
  );
}

// (The agent-side request flow lives in the engine: POST .../environment/request
// and MCP request_environment_value — the dashboard deliberately never calls them.)

function TemplateButton({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const download = useMutation({
    mutationFn: () => fetchEnvTemplate(projectId),
    onSuccess: (t) => {
      // KEY= lines only — the engine cannot include values here by construction.
      const blob = new Blob([t.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = t.filename || ".env.example";
      a.click();
      URL.revokeObjectURL(url);
      toast(`Template downloaded (${t.count} variables, no values)`, "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Template failed", "error"),
  });

  return (
    <button type="button" className="button ghost" onClick={() => download.mutate()} disabled={download.isPending}>
      {download.isPending ? "Preparing…" : "⬇ Download .env template"}
    </button>
  );
}

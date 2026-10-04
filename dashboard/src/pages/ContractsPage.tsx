/**
 * Contracts — the API contract registry (GET/POST /contracts). Registering a
 * contract is how a task declares the routes it introduces; two open tasks
 * claiming the same method+route is what the engine's conflict detector uses.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContract, fetchContracts } from "../lib/api";
import { useProject } from "./ProjectLayout";
import { useToast } from "../components/Toasts";
import { timeAgo } from "../lib/format";

const METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE"] as const;

export default function ContractsPage() {
  const { projectId } = useProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [route, setRoute] = useState("");
  const [method, setMethod] = useState<string>("GET");
  const [reqSchema, setReqSchema] = useState("{}");
  const [resSchema, setResSchema] = useState("{}");

  const contractsQ = useQuery({ queryKey: ["project", projectId, "contracts"], queryFn: ({ signal }) => fetchContracts(projectId, { signal }) });

  const register = useMutation({
    mutationFn: () =>
      createContract(projectId, {
        route: route.trim(),
        method,
        request_schema: safeParse(reqSchema),
        response_schema: safeParse(resSchema),
      }),
    onSuccess: () => {
      setRoute("");
      void qc.invalidateQueries({ queryKey: ["project", projectId, "contracts"] });
      void qc.invalidateQueries({ queryKey: ["project", projectId, "context"] });
      toast("Contract registered", "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Registration failed", "error"),
  });

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="register-contract">
        <h2 id="register-contract">Register a contract</h2>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (route.trim()) register.mutate();
          }}
        >
          <div className="inline-form">
            <label htmlFor="c-method">Method</label>
            <select id="c-method" value={method} onChange={(e) => setMethod(e.target.value)}>
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <label htmlFor="c-route">Route</label>
            <input id="c-route" required value={route} onChange={(e) => setRoute(e.target.value)} placeholder="/api/payments/checkout" />
          </div>
          <details>
            <summary>Schemas (optional JSON)</summary>
            <div className="inline-form">
              <label htmlFor="c-req">Request schema</label>
              <textarea id="c-req" rows={3} value={reqSchema} onChange={(e) => setReqSchema(e.target.value)} />
              <label htmlFor="c-res">Response schema</label>
              <textarea id="c-res" rows={3} value={resSchema} onChange={(e) => setResSchema(e.target.value)} />
            </div>
          </details>
          {register.isError && (
            <p className="error-text" role="alert">
              {register.error instanceof Error ? register.error.message : "Registration failed"}
            </p>
          )}
          <button type="submit" className="button primary" disabled={register.isPending}>
            {register.isPending ? "Registering…" : "Register contract"}
          </button>
        </form>
      </section>

      <section className="panel" aria-labelledby="contracts-list">
        <h2 id="contracts-list">Registered contracts</h2>
        {contractsQ.isLoading && <p className="muted">Loading contracts…</p>}
        {contractsQ.isError && (
          <div className="error-panel" role="alert">
            <p>Could not load contracts: {contractsQ.error instanceof Error ? contractsQ.error.message : "unknown"}</p>
            <button type="button" onClick={() => void contractsQ.refetch()}>
              Retry
            </button>
          </div>
        )}
        {contractsQ.data?.length === 0 && (
          <p className="empty">
            No contracts yet. Register the routes your feature introduces — agents check this registry before
            inventing an API shape.
          </p>
        )}
        <ul className="contract-list">
          {contractsQ.data?.map((c) => (
            <li key={c.id} className="contract">
              <b className={`method m-${c.method.toLowerCase()}`}>{c.method}</b> {c.route}
              <small className="muted"> · {timeAgo(c.created_at)}</small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function safeParse(s: string): unknown {
  try {
    return JSON.parse(s || "{}");
  } catch {
    return {}; // invalid JSON silently becomes {} — the form stays usable
  }
}

/**
 * Decisions — the decision log (GET /decisions) with a log form (POST).
 * Newest first, engine-ordered.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createDecision, fetchDecisions } from "../lib/api";
import { useProject } from "./ProjectLayout";
import { useToast } from "../components/Toasts";
import { timeAgo } from "../lib/format";

export default function DecisionsPage() {
  const { projectId } = useProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [text, setText] = useState("");
  const [reasoning, setReasoning] = useState("");

  const decisionsQ = useQuery({ queryKey: ["project", projectId, "decisions"], queryFn: ({ signal }) => fetchDecisions(projectId, { signal }) });

  const log = useMutation({
    mutationFn: () => createDecision(projectId, { text: text.trim(), reasoning: reasoning.trim() || undefined }),
    onSuccess: () => {
      setText("");
      setReasoning("");
      void qc.invalidateQueries({ queryKey: ["project", projectId, "decisions"] });
      void qc.invalidateQueries({ queryKey: ["project", projectId, "context"] });
      toast("Decision logged — every agent's context picks it up", "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Logging failed", "error"),
  });

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="log-decision">
        <h2 id="log-decision">Log a decision</h2>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) log.mutate();
          }}
        >
          <label htmlFor="dec-text">Decision</label>
          <textarea
            id="dec-text"
            required
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="We will use Supabase Auth for all human sign-in."
          />
          <label htmlFor="dec-why">Because (optional)</label>
          <input id="dec-why" value={reasoning} onChange={(e) => setReasoning(e.target.value)} placeholder="JWT verification already wired engine-side" />
          <button type="submit" className="button primary" disabled={log.isPending}>
            {log.isPending ? "Logging…" : "Log decision"}
          </button>
        </form>
      </section>

      <section className="panel" aria-labelledby="decisions-list">
        <h2 id="decisions-list">Decision log</h2>
        {decisionsQ.isLoading && <p className="muted">Loading decisions…</p>}
        {decisionsQ.isError && (
          <div className="error-panel" role="alert">
            <p>Could not load decisions: {decisionsQ.error instanceof Error ? decisionsQ.error.message : "unknown"}</p>
            <button type="button" onClick={() => void decisionsQ.refetch()}>
              Retry
            </button>
          </div>
        )}
        {decisionsQ.data?.length === 0 && (
          <p className="empty">
            No decisions yet. Log the ones your team keeps re-litigating — agents read them before writing code.
          </p>
        )}
        <ul className="decision-list">
          {decisionsQ.data?.map((d) => (
            <li key={d.id} className="decision">
              <p>{d.text}</p>
              {d.reasoning && <small>because: {d.reasoning}</small>}
              <small className="when">{timeAgo(d.created_at)}</small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

import { useState } from "react";
import {
  acceptRecommendation,
  rejectRecommendation,
  type CoordinationSummary,
  type MemberInfo,
} from "../lib/api";
import { timeAgo } from "../lib/format";

/**
 * Next Actions — Phase 4 (intelligent coordination) coordination panel.
 *
 * Renders the engine's deterministic /coordination summary: what's ready,
 * what's blocked, what needs review, what conflicts, and the one recommended
 * next step — every row carries the engine-computed reasons (nothing is
 * scored or decided client-side). Human override lives here too: Accept
 * claims the recommended task for the acting member; Reject stops suggesting
 * it to them. AI recommends, human decides — the dashboard never mutates
 * ownership without an explicit click.
 */

interface Props {
  projectId: string;
  summary: CoordinationSummary;
  members: MemberInfo[];
  onChanged: () => void;
}

const KIND_LABEL: Record<NextActionKind, string> = {
  resolve_conflict: "⚠ Resolve conflict first",
  review_task: "👀 Needs review",
  unblock_task: "🔓 Unblock this",
  start_task: "▶ Start this",
  all_clear: "✓ All clear",
};
type NextActionKind = CoordinationSummary["recommended_next_step"]["kind"];

export default function NextActions({ projectId, summary, members, onChanged }: Props) {
  const [actingAs, setActingAs] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});

  const decided = members.find((m) => m.id === actingAs);

  async function accept(taskId: string, title: string) {
    if (!actingAs) {
      setMessage({ kind: "err", text: "Pick who is acting (acting as) first." });
      return;
    }
    setBusy(taskId);
    setMessage(null);
    try {
      await acceptRecommendation(projectId, taskId, actingAs);
      setMessage({ kind: "ok", text: `“${title}” is now assigned to ${decided?.name ?? "you"}.` });
      onChanged();
    } catch (err) {
      setMessage({ kind: "err", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  async function reject(taskId: string, title: string) {
    if (!actingAs) {
      setMessage({ kind: "err", text: "Pick who is acting (acting as) first." });
      return;
    }
    setBusy(taskId);
    setMessage(null);
    try {
      await rejectRecommendation(projectId, taskId, actingAs, "declined from Next Actions panel");
      setMessage({ kind: "ok", text: `“${title}” won’t be suggested to ${decided?.name ?? "you"} for 7 days.` });
      setDismissed((d) => ({ ...d, [taskId]: true }));
      onChanged();
    } catch (err) {
      setMessage({ kind: "err", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  const step = summary.recommended_next_step;
  const c = summary.conflicts;
  const conflictCount =
    c.open_contract_conflicts.length + c.task_overlaps.length + c.contract_collisions.length + c.cross_owner_dependencies.length;

  return (
    <section className="panel next-actions">
      <h3>Next actions</h3>

      {summary.ready_to_start.length > 1 && (
        <div className="na-acting">
          <label>
            Acting as{" "}
            <select value={actingAs} onChange={(e) => setActingAs(e.target.value)}>
              <option value="">— pick a member or agent —</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} ({m.kind})
                </option>
              ))}
            </select>
          </label>
          <small>accepting assigns the task to them · rejecting hides it from their suggestions</small>
        </div>
      )}

      <div className={`na-step na-${step.kind}`}>
        <div className="na-step-head">
          <b>{KIND_LABEL[step.kind]}</b>
        </div>
        <p className="na-step-title">{step.title}</p>
        <ul className="na-reasons">
          {step.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
        {step.task && step.kind === "start_task" && (
          <div className="na-step-actions">
            <button
              disabled={busy === step.task.id || !actingAs}
              onClick={() => accept(step.task!.id, step.task!.title)}
            >
              {busy === step.task.id ? "…" : "Accept & claim"}
            </button>
            <button className="na-secondary" disabled={busy === step.task.id || !actingAs} onClick={() => reject(step.task!.id, step.task!.title)}>
              Not now
            </button>
          </div>
        )}
      </div>

      {message && <p className={message.kind === "ok" ? "na-ok" : "na-err"}>{message.text}</p>}

      <div className="na-grid">
        <div className="na-col">
          <h4>Ready to start ({summary.ready_to_start.length})</h4>
          {summary.ready_to_start.length === 0 && <p className="empty">Nothing ready right now.</p>}
          {summary.ready_to_start.map((t) => (
            <div className="na-row" key={t.id}>
              <b>{t.title}</b>
              <small>
                {t.owner_name ? `assigned: ${t.owner_name}` : "unassigned — claimable"}
                {t.downstream_open > 0 && ` · ${t.downstream_open} task(s) waiting on it`}
              </small>
              <ul className="na-reasons">
                {t.reasons.slice(0, 3).map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="na-col">
          <h4>Blocked ({summary.blocked.length})</h4>
          {summary.blocked.length === 0 && <p className="empty">Nothing blocked.</p>}
          {summary.blocked.map((b) => (
            <div className="na-row na-blocked" key={b.id}>
              <b>{b.title}</b>
              {b.owner_name && <small>owner: {b.owner_name}</small>}
              <small>
                {b.state === "BLOCKED" && b.manual_blocker
                  ? `🚧 ${b.manual_blocker}`
                  : b.waiting_on.length > 0
                    ? `waiting on: ${b.waiting_on.map((w) => w.title).join(", ")}`
                    : "blocked"}
              </small>
            </div>
          ))}
        </div>

        <div className="na-col">
          <h4>Needs review ({summary.needs_review.length})</h4>
          {summary.needs_review.length === 0 && <p className="empty">No open reviews.</p>}
          {summary.needs_review.map((r) => (
            <div className="na-row" key={r.id}>
              <b>{r.title}</b>
              <small>
                {r.owner_name ? `by ${r.owner_name}` : "unassigned"} · {timeAgo(r.created_at)}
              </small>
            </div>
          ))}
        </div>

        <div className="na-col">
          <h4>Conflicts ({conflictCount})</h4>
          {conflictCount === 0 && <p className="empty">No conflicts detected.</p>}
          {c.open_contract_conflicts.map((k) => (
            <div className="na-row na-conflict" key={k.id}>
              <b>⚠ {k.description}</b>
              <small>{timeAgo(k.created_at)}</small>
            </div>
          ))}
          {c.contract_collisions.map((k) => (
            <div className="na-row na-conflict" key={`${k.method}-${k.route}`}>
              <b>
                ⚠ {k.method} {k.route}
              </b>
              <small>two open tasks registered this contract: {k.tasks.map((t) => t.title).join(" + ")}</small>
            </div>
          ))}
          {c.task_overlaps
            .filter((o) => !dismissed[o.task_a.id] && !dismissed[o.task_b.id])
            .map((o) => (
              <div className="na-row na-overlap" key={`${o.task_a.id}-${o.task_b.id}`}>
                <b>Potential overlap detected</b>
                <small>
                  “{o.task_a.title}” vs “{o.task_b.title}” · shared: {o.shared_terms.slice(0, 4).join(", ")}
                </small>
              </div>
            ))}
          {c.cross_owner_dependencies.map((x, i) => (
            <div className="na-row na-overlap" key={i}>
              <b>
                {x.waiting_owner ?? "Someone"} is waiting on {x.blocking_owner ?? "someone else"}
              </b>
              <small>
                “{x.waiting_task.title}” depends on “{x.blocking_task.title}”
              </small>
            </div>
          ))}
        </div>
      </div>

      <small className="na-footer">
        Deterministic engine state ({timeAgo(summary.generated_at)}) — who is doing what:{" "}
        {summary.who_is_doing_what.length === 0
          ? "nobody has open work"
          : summary.who_is_doing_what
              .map((w) => `${w.name}${w.kind === "agent" ? " (agent)" : ""}: ${w.open_tasks.length}`)
              .join(" · ")}
      </small>
    </section>
  );
}

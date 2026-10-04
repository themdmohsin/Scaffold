/**
 * Overview — the "who is working on what" view plus Scaffold's directives.
 * Everything here is deterministic engine output (GET /coordination,
 * /members, /context): per-member current task, recent changes, blockers,
 * the per-member next recommended action, and open conflicts. Nothing is
 * scored or decided client-side.
 *
 * Includes the single-ready-task fix: when the engine recommends a start_task,
 * Accept & claim is ENABLED even when exactly one task is ready (the old
 * panel disabled its picker logic in that case and the buttons never worked).
 */

import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptRecommendation,
  fetchCoordination,
  fetchContext,
  fetchMembers,
  fetchTasks,
  type CoordinationSummary,
  type ContextSummary,
  type MemberInfo,
  type Task,
} from "../lib/api";
import { useProject } from "./ProjectLayout";
import { timeAgo, describeEvent } from "../lib/format";
import OverviewStrip from "../components/Overview";
import { useToast } from "../components/Toasts";

export default function OverviewPage() {
  const { projectId } = useProject();

  const context = useQuery({ queryKey: ["project", projectId, "context"], queryFn: ({ signal }) => fetchContext(projectId, { signal }) });
  const members = useQuery({
    queryKey: ["project", projectId, "members"],
    queryFn: ({ signal }) => fetchMembers(projectId, { signal }),
    retry: false,
  });
  const coordination = useQuery({
    queryKey: ["project", projectId, "coordination"],
    queryFn: ({ signal }) => fetchCoordination(projectId, { signal }),
    // Older engines (pre-Phase-4) lack this route — degrade, don't fail.
    retry: false,
  });
  const tasks = useQuery({ queryKey: ["project", projectId, "tasks"], queryFn: ({ signal }) => fetchTasks(projectId, { signal }) });

  return (
    <div className="stack">
      {context.isError && (
        <div className="panel error-panel" role="alert">
          <p>Could not load the project summary: {context.error instanceof Error ? context.error.message : "unknown error"}</p>
          <button type="button" onClick={() => void context.refetch()}>
            Retry
          </button>
        </div>
      )}
      {context.data && <OverviewStrip context={context.data} />}

      <RecommendedNext projectId={projectId} coordination={coordination.data ?? null} loading={coordination.isLoading} members={members.data} />
      <WorkNow members={members.data} tasks={tasks.data} coordination={coordination.data ?? null} membersLoading={members.isLoading} />
      <Directives coordination={coordination.data ?? null} loading={coordination.isLoading} />

      <RecentChanges context={context.data} />
    </div>
  );
}

/**
 * The recommended next step with Accept & claim / Not now. The claim identity
 * is the signed-in user's own roster row (is_me) — no "acting as" picker.
 */
function RecommendedNext({
  projectId,
  coordination,
  loading,
  members,
}: {
  projectId: string;
  coordination: CoordinationSummary | null;
  loading: boolean;
  members?: MemberInfo[];
}) {
  const { toast } = useToast();
  const qc = useQueryClient();

  const claim = useMutation({
    mutationFn: async (taskId: string) => {
      // The roster row flagged is_me is the signed-in user's own identity.
      const me = members?.find((m) => m.is_me);
      if (!me) throw new Error("Your roster row was not found — have an owner re-invite you from the Team page.");
      return acceptRecommendation(projectId, taskId, me.id);
    },
    onSuccess: () => {
      toast("Task claimed — it is now assigned to you", "success");
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Claim failed", "error"),
  });

  if (loading) return <section className="panel">Computing next actions…</section>;
  if (!coordination) return null;

  const step = coordination.recommended_next_step;
  const meExists = Boolean(members?.some((m) => m.is_me));

  return (
    <section className="panel" aria-labelledby="next-step-heading">
      <h2 id="next-step-heading">Recommended next step</h2>
      <div className={`na-step na-${step.kind}`}>
        <b>{step.title}</b>
        <ul className="na-reasons">
          {step.reasons.slice(0, 3).map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
        {step.kind === "start_task" && step.task && (
          <div className="row-end">
            <button
              type="button"
              className="button primary"
              disabled={claim.isPending}
              onClick={() => claim.mutate(step.task!.id)}
            >
              {claim.isPending ? "Claiming…" : "Accept & claim"}
            </button>
            <Link className="button ghost" to={`tasks?task=${step.task.id}`}>
              View on the board
            </Link>
          </div>
        )}
        {step.kind !== "start_task" && step.kind !== "all_clear" && step.task && (
          <p className="muted">
            Next action: <Link to={`tasks?task=${step.task.id}`}>{step.task.title}</Link>
          </p>
        )}
        {step.kind === "all_clear" && <p className="muted">Nothing pending — create more work on the Tasks page.</p>}
      </div>
      {step.kind === "start_task" && !meExists && (
        <p className="muted">Your roster row was not found (pre-auth project?). Have an owner re-invite you via the Team page.</p>
      )}
    </section>
  );
}

/** Per-member current task + blockers ("who is working on what"). */
function WorkNow({
  members,
  tasks,
  coordination,
  membersLoading,
}: {
  members?: MemberInfo[];
  tasks?: Task[];
  coordination: CoordinationSummary | null;
  membersLoading: boolean;
}) {
  if (membersLoading) return <section className="panel">Loading team activity…</section>;
  if (!members || members.length === 0) {
    return (
      <section className="panel empty-state">
        <h2>No one on the roster yet</h2>
        <p>
          Invite teammates or register agents from the <Link to="team">Team</Link> page — their current work and
          blockers will appear here.
        </p>
      </section>
    );
  }

  const blockedCounts = tasks?.filter((t) => t.is_blocked).length ?? 0;

  return (
    <section className="panel" aria-labelledby="worknow-heading">
      <h2 id="worknow-heading">Who is working on what</h2>
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">Members and their current work</caption>
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Kind</th>
              <th scope="col">Status</th>
              <th scope="col">Current task</th>
              <th scope="col">Last activity</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>
                  {m.name}
                  {m.is_me && <span className="pill">you</span>}
                </td>
                <td>
                  {m.kind === "agent" ? (
                    <span className="pill">
                      agent{m.agent_provider ? ` · ${m.agent_provider}` : ""}
                      {m.agent_model ? `/${m.agent_model}` : ""}
                    </span>
                  ) : (
                    "developer"
                  )}
                </td>
                <td>
                  <span className={`activity activity-${m.activity_status.toLowerCase()}`}>
                    {{ ACTIVE: "● Active", IDLE: "● Idle", BLOCKED: "▲ Blocked", OFFLINE: "○ Offline" }[m.activity_status]}
                  </span>
                </td>
                <td>
                  {m.current_task ? (
                    <>
                      {m.current_task.title} <span className="pill">{m.current_task.status}</span>
                      {m.current_task.status !== "done" && coordination?.blocked.some((b) => b.id === m.current_task?.id) && (
                        <span className="badge badge-blocked">blocked</span>
                      )}
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>{m.last_activity_at ? timeAgo(m.last_activity_at) : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {blockedCounts > 0 && (
        <p className="muted">
          {blockedCounts} task{blockedCounts === 1 ? "" : "s"} blocked project-wide — see <Link to="tasks">Tasks</Link>.
        </p>
      )}
    </section>
  );
}

/** Open conflicts + per-member open work (deterministic engine data). */
function Directives({
  coordination,
  loading,
}: {
  coordination: CoordinationSummary | null;
  loading: boolean;
}) {
  if (loading) return <section className="panel">Loading directives…</section>;
  if (!coordination) return null; // older engine — the panel degrades away

  const conflicts = coordination.conflicts;
  const conflictCount =
    conflicts.open_contract_conflicts.length + conflicts.task_overlaps.length + conflicts.contract_collisions.length + conflicts.cross_owner_dependencies.length;

  return (
    <section className="panel" aria-labelledby="directives-heading">
      <h2 id="directives-heading">Directives</h2>

      {conflictCount > 0 && (
        <div className="conflict-box" role="status">
          <h3>Open conflicts ({conflictCount})</h3>
          <ul>
            {conflicts.open_contract_conflicts.map((c) => (
              <li key={c.id}>{c.description ?? "Unresolved conflict"} · {timeAgo(c.created_at)}</li>
            ))}
            {conflicts.contract_collisions.map((c, i) => (
              <li key={`cc-${i}`}>
                {c.method} {c.route} registered by {c.tasks.length} open tasks
              </li>
            ))}
            {conflicts.task_overlaps.slice(0, 3).map((o, i) => (
              <li key={`ov-${i}`}>
                Potential overlap: “{o.task_a.title}” ↔ “{o.task_b.title}” (shared: {o.shared_terms.join(", ")})
              </li>
            ))}
            {conflicts.cross_owner_dependencies.slice(0, 3).map((d, i) => (
              <li key={`xd-${i}`}>
                {d.waiting_owner ?? "Someone"} is waiting on {d.blocking_owner ?? "someone"} ({d.blocking_task.title})
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">Per-member open work</caption>
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Open tasks</th>
            </tr>
          </thead>
          <tbody>
            {coordination.who_is_doing_what.map((w) => (
              <tr key={w.user_id}>
                <td>{w.name ?? "Unknown"}</td>
                <td>
                  {w.open_tasks.length === 0 ? (
                    <span className="muted">Nothing open — free to claim work</span>
                  ) : (
                    w.open_tasks.map((t) => (
                      <span key={t.id} className="chip-row">
                        {t.title} <span className="pill">{t.status}</span>
                      </span>
                    ))
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RecentChanges({ context }: { context?: ContextSummary }) {
  const events = context?.recent_events ?? [];
  return (
    <section className="panel" aria-labelledby="recent-heading">
      <h2 id="recent-heading">Recent changes</h2>
      {events.length === 0 ? (
        <p className="muted">No activity yet — changes from teammates' sessions will appear here.</p>
      ) : (
        <ul className="event-list">
          {events.slice(0, 6).map((e) => (
            <li key={e.id}>
              {describeEvent(e.type, e.payload)} <small className="muted">{timeAgo(e.created_at)}</small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

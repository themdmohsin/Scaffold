import type { ContextSummary } from "../lib/api";

interface Props {
  context: ContextSummary;
}

/**
 * Project overview — the "what is happening in this project right now?" strip.
 * Answers, at a glance: what's done, what's being done, what needs doing,
 * what's blocked, what needs review, and whether there are open conflicts.
 */
export default function Overview({ context }: Props) {
  const counts = context.task_counts ?? {
    todo: context.tasks.todo,
    in_progress: context.tasks.in_progress,
    review: 0,
    done: context.tasks.done,
    blocked: 0,
  };
  const conflicts = context.open_conflicts ?? 0;
  const total = counts.todo + counts.in_progress + counts.review + counts.done;
  const health = conflicts > 0 ? "attention" : counts.blocked > 0 ? "watch" : "on-track";
  const healthLabel = { "on-track": "On track", watch: "Blocked work", attention: "Needs attention" }[health];

  return (
    <section className="overview">
      <div className={`health health-${health}`}>
        <span className="health-dot" />
        {healthLabel}
      </div>
      <div className="stat-row">
        <Stat label="To do" value={counts.todo} />
        <Stat label="In progress" value={counts.in_progress} />
        <Stat label="In review" value={counts.review} />
        <Stat label="Done" value={counts.done} />
        <Stat label="Blocked" value={counts.blocked} warn={counts.blocked > 0} />
        <Stat label="Conflicts" value={conflicts} warn={conflicts > 0} />
      </div>
      {total > 0 && (
        <div className="progress-bar" title={`${counts.done}/${total} done`}>
          <span style={{ width: `${(counts.done / total) * 100}%` }} />
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className={`stat ${warn && value > 0 ? "stat-warn" : ""}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

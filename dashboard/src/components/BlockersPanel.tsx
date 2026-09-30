import type { BlockerInfo, Task } from "../lib/api";
import { timeAgo } from "../lib/format";

interface Props {
  blockers: BlockerInfo[];
  tasks: Task[];
}

/**
 * Splits the engine's unified blockers list into task-level blockers
 * (task_id set — someone hit "blocked" on a card) and open conflicts
 * (task_id null — the Day 4 contract-shape conflict detector). Same data,
 * same table; the split is purely presentational.
 */
export default function BlockersPanel({ blockers, tasks }: Props) {
  const taskBlockers = blockers.filter((b) => b.task_id);
  const conflicts = blockers.filter((b) => !b.task_id);

  return (
    <section className="panel">
      <h3>Blockers &amp; conflicts</h3>
      {blockers.length === 0 && <p className="empty">No open blockers — everything is moving.</p>}

      {taskBlockers.map((b) => {
        const task = tasks.find((t) => t.id === b.task_id);
        return (
          <div className="decision blocker-row" key={b.id}>
            <p>
              🚧 {b.description}
              {task && <span className="related-task"> — related to “{task.title}”</span>}
            </p>
            <small className="when">{timeAgo(b.created_at)}</small>
          </div>
        );
      })}

      {conflicts.map((b) => (
        <div className="decision conflict" key={b.id}>
          <p>⚠ {b.description}</p>
          <small className="when">{timeAgo(b.created_at)}</small>
        </div>
      ))}
    </section>
  );
}

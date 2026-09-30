import type { EventInfo, Task, UserInfo } from "../lib/api";
import { timeAgo } from "../lib/format";

interface Props {
  users: UserInfo[];
  tasks: Task[];
  recentEvents: EventInfo[];
}

/**
 * "Who's doing what right now" — deterministic, computed client-side from
 * tasks + users already on the page (no new backend concept needed). Named
 * generically ("developer/agent") so Phase 3 identity work can slot in agent
 * sessions without reshaping this component.
 */
export default function ActiveWork({ users, tasks, recentEvents }: Props) {
  const openTasksByOwner = new Map<string, Task[]>();
  for (const t of tasks) {
    if (!t.owner_id || t.status === "done") continue;
    const list = openTasksByOwner.get(t.owner_id) ?? [];
    list.push(t);
    openTasksByOwner.set(t.owner_id, list);
  }

  const taskOwnerById = new Map(tasks.map((t) => [t.id, t.owner_id] as const));
  const lastActivityByOwner = new Map<string, string>();
  for (const ev of recentEvents) {
    const taskId = (ev.payload as { task_id?: string })?.task_id;
    const owner = taskId ? taskOwnerById.get(taskId) : undefined;
    if (owner && !lastActivityByOwner.has(owner)) lastActivityByOwner.set(owner, ev.created_at);
  }

  const rows = Array.from(openTasksByOwner.entries());

  return (
    <section className="panel">
      <h3>Active work</h3>
      {rows.length === 0 && <p className="empty">Nobody has an assigned open task right now.</p>}
      {rows.map(([ownerId, ownerTasks]) => {
        const user = users.find((u) => u.id === ownerId);
        const last = lastActivityByOwner.get(ownerId);
        return (
          <div className="active-row" key={ownerId}>
            <div className="active-who">
              <span className="avatar">{(user?.name ?? "?").slice(0, 1).toUpperCase()}</span>
              <div>
                <b>{user?.name ?? "Unknown"}</b>
                {user?.role && <small className="role">{user.role}</small>}
              </div>
            </div>
            <div className="active-tasks">
              {ownerTasks.map((t) => (
                <span className={`chip chip-${t.status}`} key={t.id}>
                  {t.title}
                </span>
              ))}
            </div>
            <small className="active-last">{last ? `active ${timeAgo(last)}` : ""}</small>
          </div>
        );
      })}
    </section>
  );
}

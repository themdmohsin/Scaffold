import { useState } from "react";
import type { Task, TaskStatus, UserInfo } from "../lib/api";
import { userName } from "../lib/format";

const COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: "todo", label: "To do" },
  { status: "in_progress", label: "In progress" },
  { status: "review", label: "Review" },
  { status: "done", label: "Done" },
];

interface Props {
  tasks: Task[];
  users: UserInfo[];
  busyTask: string | null;
  onCreate: (title: string) => void;
  onMove: (task: Task, status: TaskStatus) => void;
  onSelect: (taskId: string) => void;
}

function nextStatus(s: TaskStatus): TaskStatus {
  const order: TaskStatus[] = ["todo", "in_progress", "review", "done"];
  const i = order.indexOf(s);
  return order[(i + 1) % order.length];
}

export default function TaskBoard({ tasks, users, busyTask, onCreate, onMove, onSelect }: Props) {
  const [newTitle, setNewTitle] = useState("");

  function submitNew() {
    const title = newTitle.trim();
    if (!title) return;
    onCreate(title);
    setNewTitle("");
  }

  return (
    <section className="board">
      {COLUMNS.map((col) => (
        <div className="column" key={col.status}>
          <h3>
            {col.label} <span className="pill">{tasks.filter((t) => t.status === col.status).length}</span>
          </h3>
          {col.status === "todo" && (
            <div className="add-task">
              <input
                placeholder="New task title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitNew()}
              />
              <button onClick={submitNew} disabled={!newTitle.trim()}>
                +
              </button>
            </div>
          )}
          {tasks
            .filter((t) => t.status === col.status)
            .map((t) => (
              <div
                key={t.id}
                className={`card card-${t.status} ${t.is_blocked ? "card-blocked" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(t.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelect(t.id)}
              >
                <div className="card-top">
                  <span className={`badge badge-${t.priority ?? "medium"}`}>{t.priority ?? "medium"}</span>
                  {t.is_blocked && <span className="badge badge-blocked">blocked</span>}
                </div>
                <span className="card-title">{t.title}</span>
                {t.is_blocked && t.blocked_by_dependencies && t.blocked_by_dependencies.length > 0 && (
                  <small className="blocked-by">Blocked by: {t.blocked_by_dependencies.map((d) => d.title).join(", ")}</small>
                )}
                <div className="card-meta">
                  <small>{userName(users, t.owner_id)}</small>
                  {t.due_at && <small>due {new Date(t.due_at).toLocaleDateString()}</small>}
                </div>
                <button
                  className="advance"
                  disabled={busyTask === t.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    onMove(t, nextStatus(t.status));
                  }}
                  title="Advance to the next column"
                >
                  Move to {COLUMNS[(COLUMNS.findIndex((c) => c.status === t.status) + 1) % COLUMNS.length].label} →
                </button>
              </div>
            ))}
        </div>
      ))}
    </section>
  );
}

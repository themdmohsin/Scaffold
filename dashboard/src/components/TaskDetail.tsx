import { useEffect, useState } from "react";
import type { Task, TaskPriority, TaskStatus, TaskUpdateBody, UserInfo } from "../lib/api";

interface Props {
  task: Task;
  users: UserInfo[];
  allTasks: Task[];
  saving: boolean;
  onClose: () => void;
  onSave: (patch: TaskUpdateBody) => void;
  onAddDependency: (dependsOnTaskId: string) => void;
  onRemoveDependency: (dependsOnTaskId: string) => void;
}

const STATUSES: TaskStatus[] = ["todo", "in_progress", "review", "done"];
const PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"];

/** Full task editor — title, description, status, priority, assignee,
 * manual block switch (+ reason), and dependency management. This is where
 * "Tasks can be created / edited / assigned / prioritized / blocked" lives. */
export default function TaskDetail({ task, users, allTasks, saving, onClose, onSave, onAddDependency, onRemoveDependency }: Props) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [priority, setPriority] = useState<TaskPriority>(task.priority ?? "medium");
  const [ownerId, setOwnerId] = useState(task.owner_id ?? "");
  const [blocked, setBlocked] = useState(task.blocked ?? false);
  const [blockerReason, setBlockerReason] = useState("");
  const [depToAdd, setDepToAdd] = useState("");

  useEffect(() => {
    setTitle(task.title);
    setDescription(task.description ?? "");
    setStatus(task.status);
    setPriority(task.priority ?? "medium");
    setOwnerId(task.owner_id ?? "");
    setBlocked(task.blocked ?? false);
    setBlockerReason("");
  }, [task]);

  const depIds = new Set((task.dependencies ?? []).map((d) => d.id));
  const candidates = allTasks.filter((t) => t.id !== task.id && !depIds.has(t.id));

  function save() {
    const patch: TaskUpdateBody = {};
    if (title.trim() && title !== task.title) patch.title = title.trim();
    if (description !== (task.description ?? "")) patch.description = description || null;
    if (status !== task.status) patch.status = status;
    if (priority !== (task.priority ?? "medium")) patch.priority = priority;
    if (ownerId !== (task.owner_id ?? "")) patch.owner_id = ownerId || null;
    if (blocked !== (task.blocked ?? false)) {
      patch.blocked = blocked;
      if (blocked && blockerReason.trim()) patch.blocker_reason = blockerReason.trim();
    }
    if (Object.keys(patch).length > 0) onSave(patch);
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h3>Edit task</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <label>Title</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />

        <label>Description</label>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Add detail…" />

        <div className="drawer-row">
          <div>
            <label>Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Priority</label>
            <select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
        </div>

        <label>Assignee</label>
        <select value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
          <option value="">Unassigned</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
              {u.role ? ` (${u.role})` : ""}
            </option>
          ))}
        </select>

        <label className="checkbox-row">
          <input type="checkbox" checked={blocked} onChange={(e) => setBlocked(e.target.checked)} />
          Blocked
        </label>
        {blocked && !task.blocked && (
          <input placeholder="Why is this blocked? (optional)" value={blockerReason} onChange={(e) => setBlockerReason(e.target.value)} />
        )}
        {task.blocked_by_dependencies && task.blocked_by_dependencies.length > 0 && (
          <p className="dep-blocked-note">
            Also blocked by dependency: {task.blocked_by_dependencies.map((d) => d.title).join(", ")}
          </p>
        )}

        <button className="save-btn" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </button>

        <label>Depends on</label>
        {(task.dependencies ?? []).length === 0 && <p className="empty">No dependencies.</p>}
        <ul className="dep-list">
          {(task.dependencies ?? []).map((d) => (
            <li key={d.id}>
              <span className={d.status === "done" ? "dep-done" : "dep-open"}>{d.title}</span>
              <small>{d.status}</small>
              <button className="dep-remove" onClick={() => onRemoveDependency(d.id)}>
                remove
              </button>
            </li>
          ))}
        </ul>
        {candidates.length > 0 && (
          <div className="add-dep">
            <select value={depToAdd} onChange={(e) => setDepToAdd(e.target.value)}>
              <option value="">Add a dependency…</option>
              {candidates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
            <button
              disabled={!depToAdd}
              onClick={() => {
                onAddDependency(depToAdd);
                setDepToAdd("");
              }}
            >
              Add
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}

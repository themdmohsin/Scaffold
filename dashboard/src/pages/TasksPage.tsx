/**
 * Tasks — the kanban board (TODO / IN PROGRESS / REVIEW / DONE) with:
 * - optimistic moves (the card moves instantly; a failed PATCH rolls back)
 * - the TaskDetail drawer as a real <dialog> (focus, Escape, backdrop)
 * - due-date editing (PATCH .../tasks now accepts due_at)
 * - "advance" stops at DONE — completed tasks no longer wrap to TODO
 */

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addDependency,
  createTask,
  fetchReadyTasks,
  fetchTasks,
  fetchUsers,
  patchTask,
  removeDependency,
  type ReadyTask,
  type Task,
  type TaskPriority,
  type TaskStatus,
  type TaskUpdateBody,
  type UserInfo,
} from "../lib/api";
import { useProject } from "./ProjectLayout";
import { useToast } from "../components/Toasts";
import { userName } from "../lib/format";

const COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: "todo", label: "To do" },
  { status: "in_progress", label: "In progress" },
  { status: "review", label: "Review" },
  { status: "done", label: "Done" },
];

export default function TasksPage() {
  const { projectId } = useProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [newTitle, setNewTitle] = useState("");

  const tasksQ = useQuery({ queryKey: ["project", projectId, "tasks"], queryFn: ({ signal }) => fetchTasks(projectId, { signal }) });
  const usersQ = useQuery({ queryKey: ["project", projectId, "users"], queryFn: ({ signal }) => fetchUsers(projectId, { signal }) });
  const readyQ = useQuery({ queryKey: ["project", projectId, "ready"], queryFn: ({ signal }) => fetchReadyTasks(projectId, undefined, { signal }) });

  const tasks = tasksQ.data ?? [];
  const users = usersQ.data ?? [];
  const readyIds = new Set(readyQ.data?.ready_task_ids ?? []);
  const openTaskId = searchParams.get("task");
  const openTask = tasks.find((t) => t.id === openTaskId) ?? null;

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["project", projectId, "tasks"] });
    void qc.invalidateQueries({ queryKey: ["project", projectId, "ready"] });
    void qc.invalidateQueries({ queryKey: ["project", projectId, "context"] });
    void qc.invalidateQueries({ queryKey: ["project", projectId, "coordination"] });
  };

  const move = useMutation({
    mutationFn: ({ task, status }: { task: Task; status: TaskStatus }) => patchTask(projectId, task.id, { status }),
    // Optimistic: snap the card into its new column immediately.
    onMutate: async ({ task, status }) => {
      await qc.cancelQueries({ queryKey: ["project", projectId, "tasks"] });
      const prev = qc.getQueryData<Task[]>(["project", projectId, "tasks"]);
      qc.setQueryData<Task[]>(["project", projectId, "tasks"], (old) =>
        (old ?? []).map((t) => (t.id === task.id ? { ...t, status, completed_at: status === "done" ? new Date().toISOString() : null } : t)),
      );
      return { prev };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["project", projectId, "tasks"], ctx.prev);
      toast(`Move failed: ${err instanceof Error ? err.message : "unknown error"}`, "error");
    },
    onSettled: () => invalidate(),
  });

  const create = useMutation({
    mutationFn: (title: string) => createTask(projectId, { title }),
    onSuccess: () => {
      setNewTitle("");
      invalidate();
    },
    onError: (err) => toast(`Could not create task: ${err instanceof Error ? err.message : "unknown error"}`, "error"),
  });

  const save = useMutation({
    mutationFn: ({ taskId, body }: { taskId: string; body: TaskUpdateBody }) => patchTask(projectId, taskId, body),
    onSuccess: () => {
      invalidate();
      toast("Task saved", "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Save failed", "error"),
  });

  const addDep = useMutation({
    mutationFn: ({ taskId, depId }: { taskId: string; depId: string }) => addDependency(projectId, taskId, depId),
    onSuccess: invalidate,
    onError: (err) => toast(err instanceof Error ? err.message : "Could not add dependency", "error"),
  });
  const removeDep = useMutation({
    mutationFn: ({ taskId, depId }: { taskId: string; depId: string }) => removeDependency(projectId, taskId, depId),
    onSuccess: invalidate,
    onError: (err) => toast(err instanceof Error ? err.message : "Could not remove dependency", "error"),
  });

  return (
    <div className="stack">
      {tasksQ.isError && (
        <div className="panel error-panel" role="alert">
          <p>Could not load tasks: {tasksQ.error instanceof Error ? tasksQ.error.message : "unknown error"}</p>
          <button type="button" onClick={() => void tasksQ.refetch()}>
            Retry
          </button>
        </div>
      )}
      {tasksQ.isLoading && <section className="panel">Loading board…</section>}

      {readyQ.data && readyQ.data.count > 0 && (
        <section className="panel" aria-labelledby="ready-heading">
          <h2 id="ready-heading">Ready to start ({readyQ.data.count})</h2>
          <p className="muted">Engine-ranked: unblocked work first, with the reasons it is ready.</p>
          <ul className="ready-list">
            {readyQ.data.ready_tasks.slice(0, 5).map((t: ReadyTask) => (
              <li key={t.id}>
                <button type="button" className="linklike" onClick={() => setSearchParams({ task: t.id })}>
                  {t.title}
                </button>
                <span className="pill">{t.priority}</span>
                {!t.owner_id && <span className="pill">unassigned</span>}
                <small className="muted">{t.reasons[0]}</small>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tasksQ.data && (
        <div className="board" role="list">
          {COLUMNS.map((col) => {
            const colTasks = tasks.filter((t) => t.status === col.status);
            return (
              <section key={col.status} className="column" role="listitem" aria-label={`${col.label} column`}>
                <h3>
                  {col.label} <span className="pill">{colTasks.length}</span>
                </h3>
                {col.status === "todo" && (
                  <form
                    className="add-task"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (newTitle.trim()) create.mutate(newTitle);
                    }}
                  >
                    <label htmlFor="new-task" className="sr-only">
                      New task title
                    </label>
                    <input
                      id="new-task"
                      placeholder="New task title"
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                    />
                    <button type="submit" disabled={!newTitle.trim() || create.isPending} aria-label="Create task">
                      +
                    </button>
                  </form>
                )}
                {colTasks.length === 0 && col.status === "todo" && create.isSuccess === false && tasks.length === 0 && (
                  <p className="empty">No tasks yet — add the first one above.</p>
                )}
                {colTasks.map((t) => (
                  <TaskCard
                    key={t.id}
                    task={t}
                    users={users}
                    ready={readyIds.has(t.id)}
                    busy={move.isPending && move.variables?.task.id === t.id}
                    onOpen={() => setSearchParams({ task: t.id })}
                    onMove={(status) => move.mutate({ task: t, status })}
                  />
                ))}
              </section>
            );
          })}
        </div>
      )}

      {openTask && (
        <TaskDrawer
          task={openTask}
          tasks={tasks}
          users={users}
          saving={save.isPending}
          onClose={() => setSearchParams({})}
          onSave={(body) => save.mutate({ taskId: openTask.id, body })}
          onAddDependency={(depId) => addDep.mutate({ taskId: openTask.id, depId })}
          onRemoveDependency={(depId) => removeDep.mutate({ taskId: openTask.id, depId })}
        />
      )}
    </div>
  );
}

function TaskCard({
  task,
  users,
  ready,
  busy,
  onOpen,
  onMove,
}: {
  task: Task;
  users: UserInfo[];
  ready: boolean;
  busy: boolean;
  onOpen: () => void;
  onMove: (status: TaskStatus) => void;
}) {
  // No wrap-around: the advance button disappears on DONE (was: done → todo).
  const next = COLUMNS[COLUMNS.findIndex((c) => c.status === task.status) + 1];
  return (
    <article className={`card card-${task.status} ${task.is_blocked ? "card-blocked" : ""}`} aria-label={`Task: ${task.title}`}>
      <div className="card-top">
        <span className={`badge badge-${task.priority ?? "medium"}`}>{task.priority ?? "medium"}</span>
        {ready && <span className="badge badge-ready">ready</span>}
        {task.is_blocked && <span className="badge badge-blocked">blocked</span>}
      </div>
      <button type="button" className="card-title linklike" onClick={onOpen}>
        {task.title}
      </button>
      {task.is_blocked && task.blocked_by_dependencies && task.blocked_by_dependencies.length > 0 && (
        <small className="blocked-by">Blocked by: {task.blocked_by_dependencies.map((d) => d.title).join(", ")}</small>
      )}
      <div className="card-meta">
        <small>{userName(users, task.owner_id)}</small>
        {task.due_at && <small>due {new Date(task.due_at).toLocaleDateString()}</small>}
      </div>
      {next && (
        <button type="button" className="advance" disabled={busy} onClick={() => onMove(next.status)}>
          Move to {next.label} →
        </button>
      )}
    </article>
  );
}

/** The task editor as a proper modal dialog: labelled, Escape works, focus lands inside. */
function TaskDrawer({
  task,
  tasks,
  users,
  saving,
  onClose,
  onSave,
  onAddDependency,
  onRemoveDependency,
}: {
  task: Task;
  tasks: Task[];
  users: UserInfo[];
  saving: boolean;
  onClose: () => void;
  onSave: (body: TaskUpdateBody) => void;
  onAddDependency: (depId: string) => void;
  onRemoveDependency: (depId: string) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [priority, setPriority] = useState<TaskPriority>(task.priority ?? "medium");
  const [ownerId, setOwnerId] = useState(task.owner_id ?? "");
  const [status, setStatus] = useState(task.status);
  const [dueDate, setDueDate] = useState(task.due_at ? task.due_at.slice(0, 10) : "");
  const [blocked, setBlocked] = useState(Boolean(task.blocked));
  const [blockerReason, setBlockerReason] = useState("");
  const [depToAdd, setDepToAdd] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;
    if (!dlg.open) dlg.showModal();
    const onCancel = (e: Event) => {
      e.preventDefault();
      onClose();
    };
    dlg.addEventListener("cancel", onCancel);
    return () => dlg.removeEventListener("cancel", onCancel);
  }, [onClose]);

  const candidates = tasks.filter((t) => t.id !== task.id && !(task.dependencies ?? []).some((d) => d.id === t.id));

  function submit() {
    onSave({
      title: title.trim() || task.title,
      description: description || null,
      priority: priority as TaskUpdateBody["priority"],
      owner_id: ownerId || null,
      status,
      blocked,
      blocker_reason: blocked ? blockerReason || undefined : undefined,
      // due_at: set/change supported; an emptied field keeps the old value
      // (the engine's Optional-without-clear semantics match owner_id).
      due_at: dueDate ? new Date(`${dueDate}T12:00:00`).toISOString() : task.due_at,
    });
  }

  return (
    <dialog ref={dialogRef} className="task-dialog" aria-labelledby="task-dialog-title">
      <div className="task-dialog-grid">
        <div className="task-dialog-main">
          <label htmlFor="td-title">Title</label>
          <input id="td-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <label htmlFor="td-desc">Description</label>
          <textarea id="td-desc" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
          <label htmlFor="td-due">Due date</label>
          <input id="td-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          <button type="button" className="save-btn" onClick={submit} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
        <aside className="task-dialog-side">
          <label htmlFor="td-status">Status</label>
          <select id="td-status" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
            {COLUMNS.map((c) => (
              <option key={c.status} value={c.status}>
                {c.label}
              </option>
            ))}
          </select>
          <label htmlFor="td-priority">Priority</label>
          <select id="td-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
            {["low", "medium", "high", "urgent"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <label htmlFor="td-owner">Assignee</label>
          <select id="td-owner" value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
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
            <input
              placeholder="Why is this blocked? (optional)"
              aria-label="Blocker reason"
              value={blockerReason}
              onChange={(e) => setBlockerReason(e.target.value)}
            />
          )}
          <label htmlFor="td-dep">Depends on</label>
          {(task.dependencies ?? []).length === 0 && <p className="empty">No dependencies.</p>}
          <ul className="dep-list">
            {(task.dependencies ?? []).map((d) => (
              <li key={d.id}>
                <span className={d.status === "done" ? "dep-done" : "dep-open"}>{d.title}</span>
                <small>{d.status}</small>
                <button type="button" className="dep-remove" onClick={() => onRemoveDependency(d.id)}>
                  remove
                </button>
              </li>
            ))}
          </ul>
          {candidates.length > 0 && (
            <div className="add-dep">
              <label htmlFor="td-dep" className="sr-only">
                Add a dependency
              </label>
              <select id="td-dep" value={depToAdd} onChange={(e) => setDepToAdd(e.target.value)}>
                <option value="">Add a dependency…</option>
                {candidates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
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
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </aside>
      </div>
    </dialog>
  );
}


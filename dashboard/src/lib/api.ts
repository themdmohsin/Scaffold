/**
 * Engine client — every dashboard read/write goes through the frozen API routes.
 *
 * Source of truth is the engine (docs/API_CONTRACTS.md); Supabase Realtime is only
 * the "something changed" signal that triggers a refetch here.
 */

export interface ProjectInfo {
  id: string;
  name: string;
  goal: string | null;
  deadline: string | null;
}

export type TaskStatus = "todo" | "in_progress" | "review" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";

export interface TaskDependencyRef {
  id: string;
  title: string;
  status: TaskStatus;
}

export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  owner_id: string | null;
  due_at: string | null;
  created_at: string;
  // Phase 2 additive fields — absent-safe defaults for older engines.
  description?: string | null;
  priority?: TaskPriority;
  blocked?: boolean;
  created_by?: string | null;
  completed_at?: string | null;
  dependencies?: TaskDependencyRef[];
  blocked_by_dependencies?: TaskDependencyRef[];
  is_blocked?: boolean;
}

export interface UserInfo {
  id: string;
  project_id: string | null;
  name: string;
  role: string | null;
}

export interface Decision {
  id: string;
  text: string;
  reasoning: string | null;
  made_by: string | null;
  created_at: string;
}

export interface Contract {
  id: string;
  route: string;
  method: string;
  request_schema: unknown;
  response_schema: unknown;
  created_by_task_id: string | null;
  created_at: string;
}

export interface BlockerInfo {
  id: string;
  description: string | null;
  resolved: boolean;
  created_at: string;
  // Additive — present once the engine's blockers query joins task_id through.
  task_id?: string | null;
}

export interface EventInfo {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ContextSummary {
  project: ProjectInfo;
  tasks: { todo: number; in_progress: number; done: number };
  active_tasks: Task[];
  recent_decisions: { id: string; text: string; created_at: string }[];
  relevant_contracts: { route: string; method: string }[];
  // Day 5 additive keys from GET /context — absent from older engines.
  blockers?: BlockerInfo[];
  recent_events?: EventInfo[];
  generated_at: string;
  // Phase 2 additive keys.
  task_counts?: { todo: number; in_progress: number; review: number; done: number; blocked: number };
  open_conflicts?: number;
}

export interface ReasonResponse {
  answer: string;
  suggested_tasks: { title: string; owner_id: string | null; due_at: string | null }[];
}

const ENGINE_URL = (import.meta.env.VITE_ENGINE_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `engine returned ${res.status}`);
  }
  return (await res.json()) as T;
}

export const fetchContext = (projectId: string) => api<ContextSummary>(`/projects/${projectId}/context`);
export const fetchTasks = (projectId: string) => api<Task[]>(`/projects/${projectId}/tasks`);
export const fetchDecisions = (projectId: string) => api<Decision[]>(`/projects/${projectId}/decisions`);
export const fetchContracts = (projectId: string) => api<Contract[]>(`/projects/${projectId}/contracts`);
export const fetchUsers = (projectId: string) => api<UserInfo[]>(`/projects/${projectId}/users`);

export interface TaskCreateBody {
  title: string;
  owner_id?: string | null;
  due_at?: string | null;
  description?: string | null;
  priority?: TaskPriority;
  created_by?: string | null;
  dependencies?: string[];
}

export function createTask(projectId: string, body: TaskCreateBody) {
  return api<Task>(`/projects/${projectId}/tasks`, { method: "POST", body: JSON.stringify(body) });
}

export interface TaskUpdateBody {
  status?: TaskStatus;
  owner_id?: string | null;
  title?: string;
  description?: string | null;
  priority?: TaskPriority;
  blocked?: boolean;
  blocker_reason?: string | null;
}

export function patchTask(projectId: string, taskId: string, body: TaskUpdateBody) {
  return api<Task>(`/projects/${projectId}/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function addDependency(projectId: string, taskId: string, dependsOnTaskId: string) {
  return api<Task>(`/projects/${projectId}/tasks/${taskId}/dependencies`, {
    method: "POST",
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  });
}

export function removeDependency(projectId: string, taskId: string, dependsOnTaskId: string) {
  return api<Task>(`/projects/${projectId}/tasks/${taskId}/dependencies/${dependsOnTaskId}`, { method: "DELETE" });
}

export function reason(projectId: string, prompt: string) {
  return api<ReasonResponse>(`/projects/${projectId}/reason`, { method: "POST", body: JSON.stringify({ prompt }) });
}

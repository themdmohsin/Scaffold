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
  // Phase 3 additive key — absent from older engines, hence optional.
  owner_user_id?: string | null;
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

// --- Phase 3 (team collaboration) additive types + calls -------------------
// New endpoints only (see engine/app/routes/team.py); existing task/context/
// invite calls above are untouched.

export type ActivityStatus = "ACTIVE" | "IDLE" | "BLOCKED" | "OFFLINE";

export interface MemberInfo {
  id: string;
  name: string;
  role: string | null;
  kind: "developer" | "agent";
  agent_provider: string | null;
  agent_model: string | null;
  membership_status: "active" | "removed";
  joined_at: string;
  current_task: { id: string; title: string; status: Task["status"] } | null;
  activity_status: ActivityStatus;
  last_activity_at: string | null;
}

export interface InviteInfo {
  invite_url: string;
  code: string;
  expires_at_epoch: number;
}

export const fetchMembers = (projectId: string) => api<MemberInfo[]>(`/projects/${projectId}/members`);

export function createInvite(projectId: string) {
  return api<InviteInfo>(`/projects/${projectId}/invite`, { method: "POST", body: JSON.stringify({}) });
}

export function joinProject(body: { code: string; name: string; role?: string }) {
  return api<{ user_id: string; project_id: string; name: string; role: string | null }>(
    "/projects/join",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function registerAgent(
  projectId: string,
  body: { name: string; provider?: string; model?: string; session_id?: string },
) {
  return api<MemberInfo & { is_new: boolean }>(`/projects/${projectId}/agents`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateMember(
  projectId: string,
  memberId: string,
  body: { role?: string; kind?: "developer" | "agent"; requesting_user_id?: string | null },
) {
  return api<MemberInfo>(`/projects/${projectId}/members/${memberId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function removeMember(projectId: string, memberId: string, requestingUserId?: string | null) {
  const qs = requestingUserId ? `?requesting_user_id=${encodeURIComponent(requestingUserId)}` : "";
  return api<{ id: string; membership_status: string }>(
    `/projects/${projectId}/members/${memberId}${qs}`,
    { method: "DELETE" },
  );
}

export function setProjectOwner(projectId: string, userId: string, requestingUserId?: string | null) {
  return api<{ project_id: string; owner_user_id: string }>(`/projects/${projectId}/owner`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, requesting_user_id: requestingUserId ?? null }),
  });
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

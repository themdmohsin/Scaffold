/**
 * Engine client — every dashboard read/write goes through the frozen API routes.
 *
 * Source of truth is the engine (docs/API_CONTRACTS.md); Supabase Realtime is only
 * the "something changed" signal that triggers a refetch here.
 */

import { getAccessToken } from "./supabase";

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
  // Phase 6 additive: true on the signed-in caller's own human roster row.
  is_me?: boolean;
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

export function joinProject(body: { code: string; name?: string; role?: string }) {
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

/** Thrown for any non-2xx engine response; `status` lets callers branch on 401/403. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// App registers a handler so an expired/revoked session (401) drops back to sign-in.
let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Identity is the verified Supabase session - never a body/query field.
  const token = await getAccessToken();
  const res = await fetch(`${ENGINE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler?.();
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      typeof body.detail === "string" ? body.detail : `engine returned ${res.status}`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

// --- Phase 6 (authentication) calls -----------------------------------------

export interface Membership {
  project_id: string;
  supabase_role: "owner" | "admin" | "member";
  joined_at: string | null;
}

export interface Me {
  account_id: string;
  via: "jwt" | "pat";
  email: string | null;
  full_name: string | null;
  auth_provider: string | null;
  token_scoped_project_id: string | null;
  memberships: Membership[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  goal: string | null;
  deadline: string | null;
  created_at: string;
  supabase_role: Membership["supabase_role"];
  joined_at: string | null;
}

export interface TokenInfo {
  id: string;
  name: string;
  token_prefix: string;
  project_id: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export const fetchMe = () => api<Me>("/auth/me");
export const fetchProjects = () => api<{ projects: ProjectSummary[] }>("/projects").then((r) => r.projects);

export function createProject(body: { name: string; goal?: string | null }) {
  return api<{ id: string; name: string }>("/projects", { method: "POST", body: JSON.stringify(body) });
}

export const fetchTokens = () => api<TokenInfo[]>("/auth/tokens");

export function createToken(body: { name: string; project_id?: string | null }) {
  return api<TokenInfo & { token: string; warning: string }>("/auth/tokens", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const revokeToken = (id: string) =>
  api<{ revoked: boolean; id: string }>(`/auth/tokens/${id}`, { method: "DELETE" });
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

// --- Phase 4 (intelligent coordination) additive types + calls -------------
// All decision logic is engine-side and deterministic; the dashboard only
// renders it. No LLM is involved unless a user clicks "Explain".

export type CoordinationState = "READY" | "BLOCKED" | "WAITING_ON_DEPENDENCY" | "IN_PROGRESS" | "REVIEW" | "DONE";

export interface ReadyTask {
  id: string;
  title: string;
  status: TaskStatus;
  priority: TaskPriority;
  owner_id: string | null;
  owner_name?: string | null;
  owner_kind?: string | null;
  score: number;
  score_factors: Record<string, number>;
  reasons: string[];
  downstream_open: number;
  dependencies?: { id: string; title: string; status: TaskStatus }[];
  dependency_count?: number;
}

export interface BlockedTaskRow {
  id: string;
  title: string;
  state: "BLOCKED" | "WAITING_ON_DEPENDENCY";
  priority: TaskPriority;
  owner_id: string | null;
  owner_name?: string | null;
  waiting_on: { id: string; title: string; status: TaskStatus }[];
  manual_blocker?: string | null;
}

export interface TaskOverlap {
  task_a: { id: string; title: string; status: string; owner_id: string | null };
  task_b: { id: string; title: string; status: string; owner_id: string | null };
  shared_terms: string[];
  similarity: number;
}

export interface ContractCollision {
  route: string;
  method: string;
  tasks: { id: string; title: string; status: string; owner_id: string | null }[];
}

export interface CrossOwnerDependency {
  waiting_task: { id: string; title: string; owner_id: string | null };
  waiting_owner: string | null;
  blocking_task: { id: string; title: string; owner_id: string | null };
  blocking_owner: string | null;
}

export interface NextAction {
  kind: "resolve_conflict" | "review_task" | "unblock_task" | "start_task" | "all_clear";
  title: string;
  task: { id: string; title: string; status: string; owner_id: string | null; priority: TaskPriority; state: CoordinationState; downstream_open: number } | null;
  conflict?: { id: string; description: string | null; created_at: string } | null;
  reasons: string[];
}

export interface CoordinationSummary {
  project_id: string;
  task_states: Record<CoordinationState, number>;
  ready_to_start: ReadyTask[];
  blocked: BlockedTaskRow[];
  needs_review: { id: string; title: string; owner_id: string | null; owner_name?: string | null; created_at: string }[];
  conflicts: {
    open_contract_conflicts: { id: string; description: string | null; created_at: string }[];
    task_overlaps: TaskOverlap[];
    contract_collisions: ContractCollision[];
    cross_owner_dependencies: CrossOwnerDependency[];
  };
  recommended_next_step: NextAction;
  who_is_doing_what: { user_id: string; name: string | null; kind: "developer" | "agent"; open_tasks: { id: string; title: string; status: TaskStatus }[] }[];
  generated_at: string;
}

export interface Recommendation {
  task: { id: string; title: string; status: TaskStatus; priority: TaskPriority; owner_id: string | null; due_at: string | null } | null;
  state: CoordinationState;
  score: number;
  score_factors: Record<string, number>;
  reasons: string[];
  downstream_open: number;
  explanation?: string | null;
}

export interface RecommendationResponse {
  project_id: string;
  user: { id: string; name: string; kind: "developer" | "agent" } | null;
  recommendation: Recommendation | null;
  alternates: Recommendation[];
  current_work: { id: string; title: string; status: TaskStatus; state: CoordinationState }[];
  blocked_work: ({ id: string; title: string; status: TaskStatus; state: CoordinationState; waiting_on: string[] })[];
  claimable_count: number;
  note?: string;
  conflict_awareness: {
    cross_owner_dependencies: CrossOwnerDependency[];
    contract_collisions: ContractCollision[];
    open_conflicts: { id: string; description: string | null; created_at: string }[];
  };
  generated_at: string;
}

export const fetchCoordination = (projectId: string) =>
  api<CoordinationSummary>(`/projects/${projectId}/coordination`);

export const fetchRecommendation = (projectId: string, userId?: string | null) =>
  api<RecommendationResponse>(
    `/projects/${projectId}/recommendations${userId ? `?user_id=${encodeURIComponent(userId)}` : ""}`,
  );

export const fetchNextAction = (projectId: string, userId?: string | null) => {
  const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  return api<{ project_id: string; action: NextAction; task_states: Record<CoordinationState, number> }>(
    `/projects/${projectId}/recommendations/next${qs}`,
  );
};

export function acceptRecommendation(projectId: string, taskId: string, userId: string) {
  return api<{ accepted: boolean; task_id: string; idempotent: boolean }>(
    `/projects/${projectId}/tasks/${taskId}/accept-recommendation`,
    { method: "POST", body: JSON.stringify({ user_id: userId }) },
  );
}

export function rejectRecommendation(projectId: string, taskId: string, userId: string, note?: string) {
  return api<{ rejected: boolean; task_id: string }>(
    `/projects/${projectId}/tasks/${taskId}/reject-recommendation`,
    { method: "POST", body: JSON.stringify({ user_id: userId, note: note ?? null }) },
  );
}

// --- Phase 5 (secure environment) additive types + calls --------------------
// STATUS only: no response in this section can carry a secret value. Values
// reach authorized runtimes via POST .../environment/request | /pull (engine),
// which the dashboard never calls.

export interface EnvVariableInfo {
  id: string;
  project_id: string;
  key: string;
  description: string | null;
  required: boolean;
  is_secret: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  configured: boolean;
  status: "configured" | "required_missing" | "optional_missing";
  display_status: string;
}

export interface EnvironmentSummary {
  project_id: string;
  project_name: string;
  variables: EnvVariableInfo[];
  summary: { total: number; configured: number; required_missing: number; optional_missing: number };
}

export interface EnvGrantInfo {
  id: string;
  environment_variable_id: string;
  key: string;
  user_id: string;
  user_name: string | null;
  granted_by: string | null;
  created_at: string;
}

export interface EnvTemplate {
  project_id: string;
  project_name: string | null;
  filename: string;
  variables: { key: string; description: string | null }[];
  content: string;
  count: number;
}

export const fetchEnvironment = (projectId: string) =>
  api<EnvironmentSummary>(`/projects/${projectId}/environment`);

export interface EnvVariableCreateBody {
  key: string;
  description?: string;
  required?: boolean;
  is_secret?: boolean;
  created_by?: string;
  // One-way: consumed by the engine's secret store, never returned by any GET.
  value?: string;
}

export function createEnvVariable(projectId: string, body: EnvVariableCreateBody) {
  return api<EnvVariableInfo>(`/projects/${projectId}/environment/variables`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface EnvVariablePatchBody {
  description?: string;
  required?: boolean;
  is_secret?: boolean;
  // value + value_changed=true sets (or ROTATES) the stored value; permissions
  // and metadata are untouched (Phase 5 Feature 9).
  value?: string;
  value_changed?: boolean;
  requesting_user_id?: string;
}

export function patchEnvVariable(projectId: string, variableId: string, body: EnvVariablePatchBody) {
  return api<EnvVariableInfo>(`/projects/${projectId}/environment/variables/${variableId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function removeEnvVariable(projectId: string, variableId: string, requestingUserId?: string) {
  const qs = requestingUserId ? `?requesting_user_id=${encodeURIComponent(requestingUserId)}` : "";
  return api<{ removed: boolean; id: string; key: string }>(
    `/projects/${projectId}/environment/variables/${variableId}${qs}`,
    { method: "DELETE" },
  );
}

export interface EnvGrantBody {
  environment_variable_id: string;
  user_id: string;
  granted_by?: string;
  requesting_user_id?: string;
}

export function grantEnvAccess(projectId: string, body: EnvGrantBody) {
  return api<EnvGrantInfo>(`/projects/${projectId}/environment/access`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const fetchEnvAccess = (projectId: string, variableId: string) =>
  api<EnvGrantInfo[]>(
    `/projects/${projectId}/environment/access?environment_variable_id=${encodeURIComponent(variableId)}`,
  );

export function revokeEnvAccess(projectId: string, grantId: string, requestingUserId?: string) {
  const qs = requestingUserId ? `?requesting_user_id=${encodeURIComponent(requestingUserId)}` : "";
  return api<{ revoked: boolean; id: string }>(
    `/projects/${projectId}/environment/access/${grantId}${qs}`,
    { method: "DELETE" },
  );
}

export const fetchEnvTemplate = (projectId: string) =>
  api<EnvTemplate>(`/projects/${projectId}/environment/template`);


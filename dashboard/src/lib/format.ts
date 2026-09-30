/** Small formatting helpers shared across dashboard components. */

import type { UserInfo } from "./api";

export function timeAgo(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function userName(users: UserInfo[], id: string | null | undefined): string {
  if (!id) return "Unassigned";
  return users.find((u) => u.id === id)?.name ?? "Unknown";
}

export function userInitial(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || "?";
}

const EVENT_LABELS: Record<string, (p: Record<string, unknown>) => string> = {
  task_created: (p) => `Task created: "${p.title ?? ""}"`,
  task_status_changed: (p) => `"${p.title ?? ""}" moved ${p.from ?? "?"} → ${p.to ?? "?"}`,
  task_completed: (p) => `"${p.title ?? ""}" marked done`,
  task_assigned: (p) => `"${p.title ?? ""}" assigned`,
  task_priority_changed: (p) => `"${p.title ?? ""}" priority ${p.from ?? "?"} → ${p.to ?? "?"}`,
  task_blocked: (p) => `"${p.title ?? ""}" marked blocked${p.reason ? `: ${p.reason}` : ""}`,
  task_unblocked: (p) => `"${p.title ?? ""}" unblocked`,
  task_dependency_added: () => "Dependency added",
  task_dependency_removed: () => "Dependency removed",
  task_updated: (p) => `Task updated: "${p.title ?? p.task_id ?? ""}"`,
  decision_logged: () => "Decision logged",
  teammate_joined: (p) => `${p.name ?? "Someone"} joined the project`,
  conflict_flagged: () => "Conflict flagged",
  commit_ingested: () => "Commit ingested",
  change_reported: () => "Change reported by an agent",
};

export function describeEvent(type: string, payload: Record<string, unknown>): string {
  const fn = EVENT_LABELS[type];
  if (fn) {
    try {
      return fn(payload ?? {});
    } catch {
      /* fall through to the generic label below */
    }
  }
  return type.replace(/_/g, " ");
}

/**
 * Supabase Realtime subscription — the live-update signal.
 *
 * Subscribes to INSERT/UPDATE/DELETE on tasks, decisions and events for one
 * project and invokes onChange so the dashboard refetches from the engine.
 * Returns null when the VITE_SUPABASE_* build vars are missing (realtime simply
 * stays off; the dashboard still works).
 */

import type { RealtimeChannel } from "@supabase/supabase-js";
import { supabase } from "./supabase";

export interface RealtimeHandle {
  unsubscribe: () => void;
}

// Phase 2: blockers/task_dependencies joined the supabase_realtime publication
// (migrate_phase2.sql) so the Blockers panel and dependency chips update live too.
const TABLES = ["tasks", "decisions", "events", "blockers", "task_dependencies"] as const;

export function subscribeToProject(
  projectId: string,
  onChange: (table: string) => void,
  onStatus?: (status: string) => void,
): RealtimeHandle | null {
  // Shared client carries the signed-in session, so RLS scopes the feed to the user's projects.
  const client = supabase;
  if (!client) return null;

  let channel: RealtimeChannel = client.channel(`scaffold:${projectId}`);
  for (const table of TABLES) {
    channel = channel
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table, filter: `project_id=eq.${projectId}` },
        () => onChange(table),
      )
      // DELETE payloads carry only the old row's PK (default replica identity),
      // so a server-side project_id filter would drop them. Subscribe unfiltered
      // for deletes; the onChange refetch is project-scoped anyway.
      .on("postgres_changes", { event: "DELETE", schema: "public", table }, () => onChange(table));
  }
  channel.subscribe((status) => onStatus?.(status));

  return {
    unsubscribe: () => {
      void client.removeChannel(channel);
    },
  };
}

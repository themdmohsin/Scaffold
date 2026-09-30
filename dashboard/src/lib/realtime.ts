/**
 * Supabase Realtime subscription — the live-update signal.
 *
 * Subscribes to INSERT/UPDATE/DELETE on tasks, decisions and events for one
 * project and invokes onChange so the dashboard refetches from the engine.
 * Returns null when the VITE_SUPABASE_* env vars are missing (realtime simply
 * stays off; the dashboard still works).
 */

import { createClient, type RealtimeChannel, type SupabaseClient } from "@supabase/supabase-js";

export interface RealtimeHandle {
  unsubscribe: () => void;
}

// Phase 2: blockers/task_dependencies joined the supabase_realtime publication
// (migrate_phase2.sql) so the Blockers panel and dependency chips update live too.
const TABLES = ["tasks", "decisions", "events", "blockers", "task_dependencies"] as const;

export function subscribeToProject(
  projectId: string,
  onChange: () => void,
  onStatus?: (status: string) => void,
): RealtimeHandle | null {
  const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;
  if (!url || !anonKey) return null;

  let client: SupabaseClient;
  try {
    client = createClient(url, anonKey, { realtime: { params: { eventsPerSecond: 5 } } });
  } catch {
    return null;
  }

  let channel: RealtimeChannel = client.channel(`scaffold:${projectId}`);
  for (const table of TABLES) {
    channel = channel
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table, filter: `project_id=eq.${projectId}` },
        onChange,
      )
      // DELETE payloads carry only the old row's PK (default replica identity),
      // so a server-side project_id filter would drop them. Subscribe unfiltered
      // for deletes; the onChange refetch is project-scoped anyway.
      .on("postgres_changes", { event: "DELETE", schema: "public", table }, onChange);
  }
  channel.subscribe((status) => onStatus?.(status));

  return {
    unsubscribe: () => {
      void client.removeChannel(channel);
    },
  };
}

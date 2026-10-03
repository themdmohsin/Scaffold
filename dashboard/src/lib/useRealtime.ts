/**
 * Realtime → React Query bridge.
 *
 * Supabase Realtime is only the "something changed" signal: on any change we
 * invalidate the matching React Query keys and let the engine's HTTP responses
 * be the single source of truth.
 *
 * Storm control: Postgres can deliver bursts (a single PATCH writes task_updated
 * + task_status_changed; a batch import can fire dozens). Raw per-event
 * invalidation would refetch-storm the engine, so changes are coalesced in a
 * 600ms window: everything arriving inside the window invalidates once, and
 * overlapping windows collapse (a trailing window only re-arms if new keys
 * arrived while the previous was still hot).
 */

import { useEffect, useRef, useState } from "react";
import { queryClient } from "./queryClient";
import { subscribeToProject, type RealtimeHandle } from "./realtime";

/** Map a changed table to the query keys that must refetch. */
const TABLE_TO_KEYS: Record<string, string[]> = {
  tasks: ["project"],
  task_dependencies: ["project"],
  blockers: ["project"],
  decisions: ["project"],
  events: ["project"],
  users: ["project"],
  environment_variables: ["project"],
  environment_access: ["project"],
};

const STORM_WINDOW_MS = 600;

export type RealtimeHealth = "off" | "connecting" | "live" | "error";

export function useProjectRealtime(projectId: string | undefined): RealtimeHealth {
  const [health, setHealth] = useState<RealtimeHealth>("connecting");
  const pending = useRef<Set<string>>(new Set());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!projectId) return;
    pending.current = new Set();
    let handle: RealtimeHandle | null = null;
    let disposed = false;

    const flush = () => {
      flushTimer.current = null;
      const keys = new Set(pending.current);
      pending.current = new Set();
      for (const key of keys) {
        void queryClient.invalidateQueries({ queryKey: [key, projectId] });
      }
    };

    const schedule = (table: string) => {
      for (const key of TABLE_TO_KEYS[table] ?? ["project"]) {
        pending.current.add(key);
      }
      if (flushTimer.current === null) {
        flushTimer.current = setTimeout(flush, STORM_WINDOW_MS);
      }
    };

    handle = subscribeToProject(
      projectId,
      (table: string) => schedule(table),
      (status: string) => {
        if (disposed) return;
        if (status === "SUBSCRIBED") setHealth("live");
        else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") setHealth("error");
        else setHealth("connecting");
      },
    );
    if (!handle) setHealth("off");

    return () => {
      disposed = true;
      if (flushTimer.current !== null) {
        clearTimeout(flushTimer.current);
        flushTimer.current = null;
      }
      handle?.unsubscribe();
    };
  }, [projectId]);

  return health;
}

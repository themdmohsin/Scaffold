/**
 * Activity — the project event feed (GET /context → recent_events, refreshed
 * by realtime). The engine is the single source of truth; this page only
 * renders its event types with human labels.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchContext } from "../lib/api";
import { useProject } from "./ProjectLayout";
import { describeEvent, timeAgo } from "../lib/format";

export default function ActivityPage() {
  const { projectId } = useProject();
  const contextQ = useQuery({
    queryKey: ["project", projectId, "context"],
    queryFn: ({ signal }) => fetchContext(projectId, { signal }),
  });

  const events = contextQ.data?.recent_events ?? [];

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="activity-heading">
        <h2 id="activity-heading">Activity</h2>
        {contextQ.isLoading && <p className="muted">Loading activity…</p>}
        {contextQ.isError && (
          <div className="error-panel" role="alert">
            <p>Could not load activity: {contextQ.error instanceof Error ? contextQ.error.message : "unknown"}</p>
            <button type="button" onClick={() => void contextQ.refetch()}>
              Retry
            </button>
          </div>
        )}
        {events.length === 0 && !contextQ.isLoading && (
          <p className="empty">Nothing yet — task moves, agent change reports, and commits will stream in here.</p>
        )}
        <ul className="event-list">
          {events.map((e) => (
            <li key={e.id}>
              <span className="event-type">{e.type.replace(/_/g, " ")}</span> — {describeEvent(e.type, e.payload)}
              <small className="muted"> · {timeAgo(e.created_at)}</small>
            </li>
          ))}
        </ul>
        <p className="muted">Live updates arrive via Supabase Realtime; every refetch re-reads the engine.</p>
      </section>
    </div>
  );
}

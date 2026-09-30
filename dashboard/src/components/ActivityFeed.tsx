import type { EventInfo } from "../lib/api";
import { describeEvent, timeAgo } from "../lib/format";

interface Props {
  events: EventInfo[];
}

/** Recent activity — reuses the existing events table, no new backend concept. */
export default function ActivityFeed({ events }: Props) {
  return (
    <section className="panel">
      <h3>Recent activity</h3>
      {events.length === 0 && <p className="empty">Nothing has happened yet.</p>}
      <ul className="activity-list">
        {events.map((e) => (
          <li key={e.id}>
            <span>{describeEvent(e.type, e.payload)}</span>
            <small className="when">{timeAgo(e.created_at)}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}

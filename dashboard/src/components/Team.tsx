import { useState } from "react";
import {
  createInvite,
  registerAgent,
  removeMember,
  setProjectOwner,
  updateMember,
  type MemberInfo,
} from "../lib/api";

/**
 * Team panel — Phase 3 (team collaboration). Isolated component: only talks
 * to the new additive endpoints in lib/api.ts, never touches the task board.
 *
 * Identity is the signed-in Supabase session (Phase 6); the engine enforces the
 * role gates (admin+ for member management/invites). `myRole` only decides which
 * controls to render - it is never trusted for authorization.
 */

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

const STATUS_LABEL: Record<MemberInfo["activity_status"], string> = {
  ACTIVE: "● Active",
  IDLE: "● Idle",
  BLOCKED: "▲ Blocked",
  OFFLINE: "○ Offline",
};

interface Props {
  projectId: string;
  members: MemberInfo[];
  ownerUserId: string | null;
  myRole?: "owner" | "admin" | "member";
  onChanged: () => void;
}

export default function Team({ projectId, members, ownerUserId, myRole, onChanged }: Props) {
  const canManage = myRole === "owner" || myRole === "admin";
  const [invite, setInvite] = useState<{ code: string; url: string } | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [agentName, setAgentName] = useState("");
  const [agentProvider, setAgentProvider] = useState("");
  const [agentModel, setAgentModel] = useState("");
  const [agentError, setAgentError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function generateInvite() {
    setInviteError(null);
    try {
      const res = await createInvite(projectId);
      setInvite({ code: res.code, url: res.invite_url });
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : String(err));
    }
  }

  async function doRegisterAgent() {
    setAgentError(null);
    try {
      await registerAgent(projectId, {
        name: agentName.trim(),
        provider: agentProvider.trim() || undefined,
        model: agentModel.trim() || undefined,
      });
      setAgentName("");
      setAgentProvider("");
      setAgentModel("");
      onChanged();
    } catch (err) {
      setAgentError(err instanceof Error ? err.message : String(err));
    }
  }

  async function makeOwner(memberId: string) {
    setBusyId(memberId);
    try {
      await setProjectOwner(projectId, memberId);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function changeRole(memberId: string, role: string) {
    setBusyId(memberId);
    try {
      await updateMember(projectId, memberId, { role });
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(memberId: string) {
    if (!confirm("Remove this member from the project?")) return;
    setBusyId(memberId);
    try {
      await removeMember(projectId, memberId);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="panel team-panel">
      <h3>Team</h3>

      {members.length === 0 && <p className="empty">No teammates or agents on this project yet.</p>}

      <table className="team-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
            <th>Current task</th>
            <th>Status</th>
            <th>Last activity</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.id} className={m.kind === "agent" ? "team-row-agent" : undefined}>
              <td>
                {m.kind === "agent" ? "🤖 " : ""}
                {m.name}
                {m.is_me && <span className="badge-owner"> you</span>}
                {m.id === ownerUserId && <span className="badge-owner"> owner</span>}
                {m.kind === "agent" && (m.agent_provider || m.agent_model) && (
                  <div className="team-agent-meta">
                    {[m.agent_provider, m.agent_model].filter(Boolean).join(" · ")}
                  </div>
                )}
              </td>
              <td>
                <select
                  value={m.role ?? ""}
                  disabled={busyId === m.id || !canManage}
                  onChange={(e) => changeRole(m.id, e.target.value)}
                >
                  <option value="">(none)</option>
                  <option value="owner">owner</option>
                  <option value="member">member</option>
                  <option value="agent">agent</option>
                  {m.role && !["owner", "member", "agent", ""].includes(m.role) && (
                    <option value={m.role}>{m.role}</option>
                  )}
                </select>
              </td>
              <td>{m.current_task ? m.current_task.title : <span className="empty">idle</span>}</td>
              <td className={`team-status team-status-${m.activity_status.toLowerCase()}`}>
                {STATUS_LABEL[m.activity_status]}
              </td>
              <td>{timeAgo(m.last_activity_at)}</td>
              <td className="team-row-actions">
                {canManage && m.id !== ownerUserId && (
                  <button disabled={busyId === m.id} onClick={() => makeOwner(m.id)}>
                    Make owner
                  </button>
                )}
                {canManage && m.id !== ownerUserId && (
                  <button disabled={busyId === m.id} onClick={() => remove(m.id)}>
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="team-forms">
        <div className="team-form">
          <h4>Invite a teammate</h4>
          {canManage ? (
            <button onClick={generateInvite}>Generate invite link</button>
          ) : (
            <small className="empty">Only project owners/admins can create invites.</small>
          )}
          {inviteError && <p className="ask-error">⚠ {inviteError}</p>}
          {invite && (
            <div className="invite-result">
              <code>{invite.code}</code>
              <small>Share this code — it's redeemed from the project picker ("Join with an invite code").</small>
            </div>
          )}
        </div>

        <div className="team-form">
          <h4>Register an agent</h4>
          <input placeholder="Agent name (e.g. OpenCode Agent A)" value={agentName} onChange={(e) => setAgentName(e.target.value)} />
          <input placeholder="Provider (e.g. anthropic)" value={agentProvider} onChange={(e) => setAgentProvider(e.target.value)} />
          <input placeholder="Model (e.g. claude-sonnet-5)" value={agentModel} onChange={(e) => setAgentModel(e.target.value)} />
          <button onClick={doRegisterAgent} disabled={!agentName.trim()}>
            Register agent
          </button>
          {agentError && <p className="ask-error">⚠ {agentError}</p>}
        </div>
      </div>
    </section>
  );
}

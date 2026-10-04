/**
 * Team — roster with live activity status, invite generation (copyable URL —
 * /join/:code routed properly), pending invites with redemption trail, role
 * changes, removal, and agent identity registration. Identity is the session;
 * the engine enforces the role gates.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createInvite,
  fetchInvites,
  fetchMembers,
  fetchTasks,
  registerAgent,
  removeMember,
  revokeInvite,
  updateMember,
  type InviteRecord,
} from "../lib/api";
import { useProject } from "./ProjectLayout";
import { useToast } from "../components/Toasts";
import { useConfirm } from "../components/ConfirmDialog";
import { timeAgo } from "../lib/format";

export default function TeamPage() {
  const { projectId, role } = useProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const canManage = role === "owner" || role === "admin";

  const membersQ = useQuery({ queryKey: ["project", projectId, "members"], queryFn: ({ signal }) => fetchMembers(projectId, { signal }) });
  const tasksQ = useQuery({ queryKey: ["project", projectId, "tasks"], queryFn: ({ signal }) => fetchTasks(projectId, { signal }) });
  // Admin+ only; a 403 on member accounts hides the section quietly.
  const invitesQ = useQuery<InviteRecord[]>({
    queryKey: ["project", projectId, "invites"],
    queryFn: ({ signal }) => fetchInvites(projectId, { signal }),
    retry: false,
  });

  const invalidateMembers = () => {
    void qc.invalidateQueries({ queryKey: ["project", projectId, "members"] });
    void qc.invalidateQueries({ queryKey: ["project", projectId, "invites"] });
  };

  const members = membersQ.data ?? [];

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="roster-heading">
        <h2 id="roster-heading">Roster</h2>
        {membersQ.isLoading && <p className="muted">Loading roster…</p>}
        {membersQ.isError && (
          <div className="error-panel" role="alert">
            <p>Could not load the roster: {membersQ.error instanceof Error ? membersQ.error.message : "unknown"}</p>
            <button type="button" onClick={() => void membersQ.refetch()}>
              Retry
            </button>
          </div>
        )}
        <div className="table-wrap">
          <table className="data-table">
            <caption className="sr-only">Project roster</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Kind</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Current task</th>
                <th scope="col">Joined</th>
                {canManage && (
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>
                    {m.name}
                    {m.is_me && <span className="pill">you</span>}
                  </td>
                  <td>{m.kind}</td>
                  <td>
                    {canManage ? (
                      <select
                        aria-label={`Role for ${m.name}`}
                        value={m.kind === "agent" ? m.role ?? "" : m.role ?? "member"}
                        onChange={(e) =>
                          updateMember(projectId, m.id, { role: e.target.value })
                            .then(() => {
                              invalidateMembers();
                              toast("Role updated", "success");
                            })
                            .catch((err) => toast(err instanceof Error ? err.message : "Update failed", "error"))
                        }
                      >
                        {["backend", "frontend", "member", "admin", "owner"].map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      (m.role ?? "—")
                    )}
                  </td>
                  <td>
                    <span className={`activity activity-${m.activity_status.toLowerCase()}`}>
                      {{ ACTIVE: "● Active", IDLE: "● Idle", BLOCKED: "▲ Blocked", OFFLINE: "○ Offline" }[m.activity_status]}
                    </span>
                  </td>
                  <td>{m.current_task ? m.current_task.title : <span className="muted">—</span>}</td>
                  <td>{timeAgo(m.joined_at)}</td>
                  {canManage && (
                    <td>
                      <button
                        type="button"
                        className="danger-text"
                        onClick={() =>
                          void confirm({
                            title: `Remove ${m.name} from the project?`,
                            body: "Their roster row is kept for history; they lose access immediately.",
                            confirmLabel: "Remove",
                            danger: true,
                          }).then((ok) => {
                            if (!ok) return;
                            removeMember(projectId, m.id)
                              .then(() => {
                                invalidateMembers();
                                toast("Member removed", "success");
                              })
                              .catch((err) => toast(err instanceof Error ? err.message : "Removal failed", "error"));
                          })
                        }
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tasksQ.data && members.some((m) => m.kind === "agent") && (
          <p className="muted">
            Agents appear on the roster like any teammate (Scaffold has one roster, no second identity system).
          </p>
        )}
      </section>

      {canManage && <InvitesSection projectId={projectId} invitesQ={invitesQ} invalidate={invalidateMembers} />}

      <AgentSection projectId={projectId} invalidate={invalidateMembers} />
    </div>
  );
}

function InvitesSection({
  projectId,
  invitesQ,
  invalidate,
}: {
  projectId: string;
  invitesQ: ReturnType<typeof useQuery<InviteRecord[]>>;
  invalidate: () => void;
}) {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [role, setRole] = useState<"member" | "admin">("member");
  const [maxUses, setMaxUses] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createInvite(projectId, {
        supabase_role: role,
        max_uses: maxUses ? Number(maxUses) : null,
      }),
    onSuccess: (inv) => {
      invalidate();
      toast("Invite created — share the link", "success");
      // Surface the fresh link immediately, fully qualified for the origin.
      const url = inv.invite_url.startsWith("http")
        ? inv.invite_url
        : `${window.location.origin}/join/${encodeURIComponent(inv.code)}`;
      void navigator.clipboard.writeText(url).catch(() => undefined);
      setLastUrl(url);
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Invite creation failed", "error"),
  });

  const [lastUrl, setLastUrl] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: (inviteId: string) => revokeInvite(projectId, inviteId),
    onSuccess: () => {
      invalidate();
      toast("Invite revoked", "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Revoke failed", "error"),
  });

  const invites = invitesQ.data ?? [];
  const active = invites.filter((i) => !i.revoked);

  return (
    <section className="panel" aria-labelledby="invites-heading">
      <h2 id="invites-heading">Invites</h2>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label htmlFor="inv-role">Join as</label>
        <select id="inv-role" value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
          <option value="member">member</option>
          <option value="admin">admin</option>
        </select>
        <label htmlFor="inv-uses">Max uses</label>
        <input
          id="inv-uses"
          type="number"
          min={1}
          value={maxUses}
          onChange={(e) => setMaxUses(e.target.value)}
          placeholder="unlimited"
        />
        <button type="submit" className="button primary" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create invite"}
        </button>
      </form>

      {lastUrl && (
        <div className="invite-link-box">
          <label htmlFor="invite-url">Share this link (copied to your clipboard):</label>
          <div className="copy-row">
            <code className="copy-value" id="invite-url">
              {lastUrl}
            </code>
            <button type="button" onClick={() => void navigator.clipboard.writeText(lastUrl)}>
              Copy again
            </button>
          </div>
        </div>
      )}

      {invitesQ.isError && <p className="muted">Invite history is available to admins and owners.</p>}
      {active.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="sr-only">Pending invites</caption>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Uses</th>
                <th scope="col">Expires</th>
                <th scope="col">Link</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {active.map((inv) => {
                const url = `${window.location.origin}/join/${encodeURIComponent(inv.code)}`;
                return (
                  <tr key={inv.id}>
                    <td>{inv.supabase_role}</td>
                    <td>
                      {inv.use_count}
                      {inv.max_uses ? ` / ${inv.max_uses}` : ""}
                    </td>
                    <td>{inv.expires_at ? new Date(inv.expires_at).toLocaleDateString() : "never"}</td>
                    <td>
                      <button
                        type="button"
                        className="linklike"
                        onClick={() => {
                          void navigator.clipboard.writeText(url);
                          toast("Invite link copied", "success");
                        }}
                      >
                        Copy link
                      </button>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="danger-text"
                        onClick={() =>
                          void confirm({
                            title: "Revoke this invite?",
                            body: "Nobody will be able to redeem it after this.",
                            confirmLabel: "Revoke",
                            danger: true,
                          }).then((ok) => ok && revoke.mutate(inv.id))
                        }
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {invites.some((i) => i.redemptions.length > 0) && (
        <p className="muted">
          {invites.reduce((n, i) => n + i.redemptions.length, 0)} redemption(s) recorded — newest{" "}
          {invites.flatMap((i) => i.redemptions).sort((a, b) => b.redeemed_at.localeCompare(a.redeemed_at))[0]?.email ?? ""}
        </p>
      )}
    </section>
  );
}

function AgentSection({ projectId, invalidate }: { projectId: string; invalidate: () => void }) {
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");

  const register = useMutation({
    mutationFn: () =>
      registerAgent(projectId, {
        name: name.trim(),
        provider: provider.trim() || undefined,
        model: model.trim() || undefined,
      }),
    onSuccess: () => {
      invalidate();
      setName("");
      setProvider("");
      setModel("");
      toast("Agent registered on the roster", "success");
    },
    onError: (err) => toast(err instanceof Error ? err.message : "Registration failed", "error"),
  });

  return (
    <section className="panel" aria-labelledby="agents-heading">
      <h2 id="agents-heading">Register an agent identity</h2>
      <p className="muted">
        Give an AI agent a first-class roster row so its work, blockers and recommendations show up next to
        everyone else's. Provider/model are free text — nothing vendor-specific is hardcoded.
      </p>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) register.mutate();
        }}
      >
        <label htmlFor="agent-name">Name</label>
        <input id="agent-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. refactor-bot" />
        <label htmlFor="agent-provider">Provider</label>
        <input id="agent-provider" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="anthropic" />
        <label htmlFor="agent-model">Model</label>
        <input id="agent-model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="claude-sonnet-5" />
        <button type="submit" className="button primary" disabled={register.isPending}>
          {register.isPending ? "Registering…" : "Register agent"}
        </button>
      </form>
      <p className="muted">
        Ownership transfers, if you ever need one, stay engine-gated: <code>POST /projects/:id/owner</code> (owner only).
      </p>
    </section>
  );
}

/**
 * /join/:code — invite redemption. The engine binds the CALLER's signed-in
 * identity to the project; the response's project_id is used to navigate
 * straight into the project (the old UI ignored it).
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { joinProject } from "../lib/api";
import { useToast } from "../components/Toasts";

export default function JoinPage() {
  const { code: codeParam } = useParams();
  const [code, setCode] = useState(codeParam ?? "");
  const navigate = useNavigate();
  const { toast } = useToast();
  const qc = useQueryClient();

  // /projects/join?code=... (the engine's invite_url shape) also lands here.
  useEffect(() => {
    if (!codeParam) {
      const fromQuery = new URLSearchParams(window.location.search).get("code");
      if (fromQuery) setCode(fromQuery);
    }
  }, [codeParam]);

  const join = useMutation({
    mutationFn: (c: string) => joinProject({ code: c.trim() }),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      toast(res.existing ? "You are already a member — welcome back." : "Joined! Welcome aboard.", "success");
      // THE FIX: honor the response's project_id — go straight into the project.
      navigate(`/projects/${res.project_id}/overview`, { replace: true });
    },
    onError: (err) => toast(err instanceof Error ? err.message : String(err), "error"),
  });

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          <Link to="/projects">Scaffold</Link>
        </span>
      </header>
      <main className="page page-narrow">
        <div className="page-head">
          <h1>Join a project</h1>
        </div>
        <form
          className="panel stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (code.trim()) join.mutate(code);
          }}
        >
          <p className="muted">
            Paste the invite code or the full link a teammate sent you. You'll join with your signed-in identity.
          </p>
          <label htmlFor="join-code">Invite code or link</label>
          <input
            id="join-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="e.g. <project-id>.1767225600.abcd1234 — or the full URL"
          />
          {join.isError && (
            <p className="error-text" role="alert">
              {join.error instanceof Error ? join.error.message : "Joining failed."}
            </p>
          )}
          <div className="row-end">
            <button type="submit" className="button primary" disabled={join.isPending || !code.trim()}>
              {join.isPending ? "Joining…" : "Join project"}
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}

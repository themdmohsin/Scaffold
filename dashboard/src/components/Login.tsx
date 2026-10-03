import { useState } from "react";
import { authConfigured, supabase } from "../lib/supabase";

/**
 * Sign-in / sign-up (Supabase Auth: email+password and GitHub OAuth, per
 * docs/API_CONTRACTS.md). The session JWT this produces is the credential the
 * engine verifies - there is no project-ID or "acting as" identity anymore.
 */
export default function Login({ notice }: { notice?: string | null }) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  if (!authConfigured || !supabase) {
    return (
      <section className="status error">
        ⚠ Sign-in is not configured: this dashboard was built without <code>VITE_SUPABASE_URL</code> /{" "}
        <code>VITE_SUPABASE_ANON_KEY</code>. Set them (repo-root <code>.env</code> for Docker, or{" "}
        <code>dashboard/.env</code> for <code>npm run dev</code>) and rebuild the dashboard. See docs/DEPLOYMENT.md.
      </section>
    );
  }
  const client = supabase;

  async function submit() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (mode === "signin") {
        const { error: err } = await client.auth.signInWithPassword({ email: email.trim(), password });
        if (err) throw err;
      } else {
        const { data, error: err } = await client.auth.signUp({ email: email.trim(), password });
        if (err) throw err;
        if (!data.session) setInfo("Account created. Check your email to confirm it, then sign in.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function github() {
    setError(null);
    const { error: err } = await client.auth.signInWithOAuth({
      provider: "github",
      options: { redirectTo: window.location.origin },
    });
    if (err) setError(err.message);
  }

  return (
    <section className="panel login">
      <h3>{mode === "signin" ? "Sign in" : "Create account"}</h3>
      {notice && <p className="ask-error">⚠ {notice}</p>}
      <div className="form login-form">
        <input
          type="email"
          placeholder="Email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password"
          placeholder="Password"
          autoComplete={mode === "signin" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && email.trim() && password && void submit()}
        />
        <button onClick={() => void submit()} disabled={busy || !email.trim() || !password}>
          {busy ? "…" : mode === "signin" ? "Sign in" : "Sign up"}
        </button>
        <button onClick={() => void github()} disabled={busy}>
          Continue with GitHub
        </button>
      </div>
      {error && <p className="ask-error">⚠ {error}</p>}
      {info && <p className="na-ok">{info}</p>}
      <p>
        <button className="link" onClick={() => setMode(mode === "signin" ? "signup" : "signin")}>
          {mode === "signin" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </p>
    </section>
  );
}

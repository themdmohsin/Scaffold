/**
 * E2E credential probe — creates or signs in the smoke-test Supabase user and
 * verifies the ENGINE accepts its JWT. Prints status only (never keys/tokens).
 *
 * Run: node e2e/probe-user.mjs <email> <password>
 * Reads VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY / VITE_ENGINE_URL from .env.
 */

import { readFileSync } from "node:fs";

const env = Object.fromEntries(
  readFileSync(new URL("../.env", import.meta.url), "utf8")
    .split(/\r?\n/)
    .filter((l) => l.includes("=") && !l.trim().startsWith("#"))
    .map((l) => {
      const i = l.indexOf("=");
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    }),
);

const [email, password] = process.argv.slice(2);
if (!email || !password) {
  console.error("usage: node probe-user.mjs <email> <password>");
  process.exit(2);
}

const base = env.VITE_SUPABASE_URL;
const anon = env.VITE_SUPABASE_ANON_KEY;
if (!base || !anon) {
  console.error("VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY missing from dashboard/.env");
  process.exit(2);
}

async function supabase(path, body) {
  const res = await fetch(`${base}/auth/v1/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: anon, Authorization: `Bearer ${anon}` },
    body: JSON.stringify(body),
  });
  return { status: res.status, json: await res.json().catch(() => ({})) };
}

// 1) try sign-in, fall back to sign-up
let auth = await supabase("token?grant_type=password", { email, password });
let mode = "signin";
if (auth.status !== 200) {
  auth = await supabase("signup", { email, password });
  mode = "signup";
}
const session = auth.json?.access_token ? auth.json : auth.json?.data?.session;
if (!session?.access_token) {
  console.log(`auth ${mode}: ${auth.status} — no session (email confirmation required?)`, JSON.stringify(auth.json?.msg ?? auth.json?.error_description ?? auth.json?.message ?? ""));
  process.exit(1);
}
console.log(`auth ${mode}: ${auth.status} — session OK, provider=${session.user?.app_metadata?.provider ?? "email"}`);

// 2) engine acceptance
const me = await fetch(`${env.VITE_ENGINE_URL ?? "http://localhost:8000"}/auth/me`, {
  headers: { Authorization: `Bearer ${session.access_token}` },
});
const meBody = await me.json().catch(() => ({}));
console.log(`engine /auth/me: ${me.status} — account_id=${meBody.account_id ?? "n/a"} memberships=${meBody.memberships?.length ?? "?"}`);
process.exit(me.status === 200 ? 0 : 1);

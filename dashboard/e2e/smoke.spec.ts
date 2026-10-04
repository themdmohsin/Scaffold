import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * Playwright smoke (requirement 9, "if feasible"). NOT part of `npm test` —
 * needs real browsers (`npx playwright install`) and a running stack:
 *
 *   engine on :8000 (or VITE_ENGINE_URL) + `npm run dev` on :5173
 *   E2E_EMAIL / E2E_PASSWORD — a Supabase Auth test user
 *   (create/verify one with: node e2e/probe-user.mjs <email> <password>)
 *
 * Skips loudly when prerequisites are missing, so CI without browsers or a
 * stack stays green.
 */

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

test.skip(!EMAIL || !PASSWORD, "E2E_EMAIL / E2E_PASSWORD not set — smoke skipped (unit tests cover the gate).");

// --- helpers -----------------------------------------------------------------

/** dashboard/.env values (VITE_*) — read at runtime, never printed. */
function loadEnv(): Record<string, string> {
  return Object.fromEntries(
    readFileSync(new URL("../.env", import.meta.url), "utf8")
      .split(/\r?\n/)
      .filter((l) => l.includes("=") && !l.trim().startsWith("#"))
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
      }),
  );
}

interface ApiSession {
  token: string;
  engine: string;
}

/** Supabase password sign-in against the live project (Node-side, no browser). */
async function apiSession(): Promise<ApiSession> {
  const env = loadEnv();
  const res = await fetch(`${env.VITE_SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: env.VITE_SUPABASE_ANON_KEY },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  const body = (await res.json()) as { access_token?: string };
  if (!res.ok || !body.access_token) throw new Error(`supabase sign-in failed: ${res.status}`);
  return { token: body.access_token, engine: env.VITE_ENGINE_URL ?? "http://localhost:8000" };
}

async function enginePost(session: ApiSession, path: string, body: unknown): Promise<Record<string, unknown>> {
  const res = await fetch(`${session.engine}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token}` },
    body: JSON.stringify(body),
  });
  const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error(`POST ${path} -> ${res.status}: ${JSON.stringify(json)}`);
  return json;
}

/** Sign in through the real UI (also exercises the login form). */
async function uiSignIn(page: Page) {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: /your projects/i })).toBeVisible({ timeout: 15_000 });
}

// --- the smoke path ----------------------------------------------------------

test("auth gate → sign in → create project wizard shows connect instructions", async ({ page }) => {
  await page.goto("/projects");
  // Signed out → login screen (the gate). No app content leaks.
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
  await expect(page.getByText("Your projects")).toHaveCount(0);

  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: /your projects/i })).toBeVisible({ timeout: 15_000 });

  // Create-project wizard.
  await page.getByRole("link", { name: /new project/i }).click();
  const name = `e2e-smoke-${Date.now()}`;
  await page.getByLabel(/project name/i).fill(name);
  await page.getByRole("button", { name: /create project/i }).click();
  await expect(page.getByText(/connect your client/i)).toBeVisible({ timeout: 15_000 });
});

test("deep links + refresh survive on every project route", async ({ page }) => {
  const session = await apiSession();
  const created = await enginePost(session, "/projects", { name: `e2e-deeplink-${Date.now()}` });
  const pid = String(created.id);

  await uiSignIn(page);

  // /projects renders the list.
  await expect(page.getByRole("heading", { name: /your projects/i })).toBeVisible();

  const routes: [string, RegExp][] = [
    ["overview", /recommended next step/i],
    ["tasks", /to do/i],
    ["team", /roster/i],
    ["environment", /configuration status/i],
  ];
  for (const [route, marker] of routes) {
    // Deep link straight to the route (fresh document, SPA fallback).
    await page.goto(`/projects/${pid}/${route}`);
    await expect(page.getByRole("heading", { name: marker })).toBeVisible({ timeout: 15_000 });
    // Refresh: the router must land back on the same view.
    await page.reload();
    await expect(page.getByRole("heading", { name: marker })).toBeVisible({ timeout: 15_000 });
  }
});

test("invite deep link /join/:code lands inside the returned project", async ({ page }) => {
  const session = await apiSession();
  const created = await enginePost(session, "/projects", { name: `e2e-join-${Date.now()}` });
  const pid = String(created.id);
  const invite = await enginePost(session, `/projects/${pid}/invite`, {});
  const code = String(invite.code);

  await uiSignIn(page);
  await page.goto(`/join/${code}`);
  await page.getByRole("button", { name: /join project/i }).click();

  // THE FIX under test: the join response's project_id drives the navigation.
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/overview`), { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /recommended next step/i })).toBeVisible({ timeout: 15_000 });
  // And a refresh inside the project keeps working.
  await page.reload();
  await expect(page.getByRole("heading", { name: /recommended next step/i })).toBeVisible({ timeout: 15_000 });
});

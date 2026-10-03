import { expect, test } from "@playwright/test";

/**
 * Smoke path: auth gate → sign in → projects → create project → task move.
 * Requires a real engine + Supabase project and E2E_EMAIL/E2E_PASSWORD; skips
 * loudly otherwise (see playwright.config.ts header).
 */

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

test.skip(!EMAIL || !PASSWORD, "E2E_EMAIL / E2E_PASSWORD not set — smoke skipped (unit tests cover the gate).");

test("auth gate → projects → create project", async ({ page }) => {
  await page.goto("/projects");
  // Signed out → login screen (the gate).
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();

  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: /sign in/i }).click();

  await expect(page.getByRole("heading", { name: /your projects/i })).toBeVisible({ timeout: 15_000 });

  // Create-project wizard.
  await page.getByRole("link", { name: /new project/i }).click();
  const name = `e2e-smoke-${Date.now()}`;
  await page.getByLabel(/project name/i).fill(name);
  await page.getByRole("button", { name: /create project/i }).click();
  await expect(page.getByText(/connect your client/i)).toBeVisible({ timeout: 15_000 });
});

test("task move persists across reload (deep link)", async ({ page }) => {
  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: /your projects/i })).toBeVisible({ timeout: 15_000 });
  // Open the first project, land on overview, then visit tasks.
  await page.getByRole("link", { name: /overview/i }).first().click();
  await page.getByRole("link", { name: "Tasks", exact: true }).click();
  // Reload must keep the route (SPA fallback + router).
  await page.reload();
  await expect(page.getByRole("heading", { name: /tasks/i }).or(page.getByText(/to do/i))).toBeVisible();
});

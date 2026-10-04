import { defineConfig } from "@playwright/test";

/**
 * Playwright smoke (requirement 9, "if feasible"). NOT part of `npm test` —
 * needs real browsers (`npx playwright install`) and a running stack:
 *
 *   engine on :8000 (or VITE_ENGINE_URL) + `npm run dev` on :5173
 *   E2E_EMAIL / E2E_PASSWORD — an existing Supabase Auth test user.
 *
 * Skipped loudly when the prerequisites are missing, so CI without browsers
 * stays green.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});

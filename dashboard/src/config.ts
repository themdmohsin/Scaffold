/**
 * Build-time configuration. Vite inlines `import.meta.env.*` at build time —
 * there is no way to change them after the fact, which is exactly why a bad
 * build must fail loudly here instead of quietly pointing at localhost.
 */

const rawEngineUrl = (import.meta.env.VITE_ENGINE_URL as string | undefined)?.trim();

/**
 * Production builds REQUIRE an explicit VITE_ENGINE_URL. A missing value must
 * throw at module load — not silently default to http://localhost:8000 (that
 * failure mode looks like "the dashboard is up but every request fails" in a
 * browser far away from the deploy pipeline).
 */
export function requireEngineUrl(): string {
  if (rawEngineUrl) return rawEngineUrl.replace(/\/+$/, "");
  const isProd = Boolean(import.meta.env.PROD);
  if (isProd) {
    throw new Error(
      "VITE_ENGINE_URL is not set. Production builds must point at the deployed engine — " +
        "set VITE_ENGINE_URL at build time (dashboard/.env or repo-root .env for Docker) and rebuild. " +
        "Refusing to fall back to http://localhost:8000.",
    );
  }
  // Dev only: make the default visible instead of silent.
  // eslint-disable-next-line no-console
  console.warn("[scaffold] VITE_ENGINE_URL not set — dev default http://localhost:8000");
  return "http://localhost:8000";
}

export const ENGINE_URL = requireEngineUrl();

/** True in production builds (import.meta.env.PROD from Vite). */
export const IS_PROD_BUILD = Boolean(import.meta.env.PROD);

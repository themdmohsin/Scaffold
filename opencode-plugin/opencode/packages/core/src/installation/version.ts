import { readFileSync } from "node:fs"

declare global {
  const OPENCODE_VERSION: string
  const OPENCODE_CHANNEL: string
}

/**
 * Scaffold fork: release builds still inject OPENCODE_VERSION at build time.
 * Source runs (bun run src/index.ts) previously printed "local"; per the
 * rebrand requirement the fallback now reads the real version from
 * packages/opencode/package.json, so `scaffold --version` always reports a
 * version that matches the tree. Falls back to "0.0.0" only if the monorepo
 * layout is unavailable (e.g. a bundled copy outside the repo).
 */
function readPackageVersion(): string | null {
  try {
    const raw = readFileSync(new URL("../../../opencode/package.json", import.meta.url), "utf8")
    const parsed = JSON.parse(raw) as { version?: unknown }
    return typeof parsed.version === "string" && parsed.version.trim() ? parsed.version.trim() : null
  } catch {
    return null
  }
}

export const InstallationVersion =
  typeof OPENCODE_VERSION === "string" ? OPENCODE_VERSION : (readPackageVersion() ?? "0.0.0")

export const InstallationChannel = typeof OPENCODE_CHANNEL === "string" ? OPENCODE_CHANNEL : "local"
export const InstallationLocal = InstallationChannel === "local"

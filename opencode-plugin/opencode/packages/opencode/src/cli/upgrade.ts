/**
 * Periodic auto-update check — DISABLED in the Scaffold fork.
 *
 * Upstream's check polls the OpenCode release channel and can install a newer
 * upstream binary in place of this client. Scaffold must never be replaced by
 * upstream OpenCode, so the check is a no-op here. The single real choke point
 * is Installation.upgrade (see installation/index.ts); this early no-op also
 * keeps the "update available" banner from ever appearing for upstream.
 */
export async function upgrade() {
  return
}

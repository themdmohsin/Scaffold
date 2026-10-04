/**
 * Single shared Supabase client for the dashboard (Phase 6).
 *
 * It does two jobs: Supabase Auth (the session JWT is what the engine verifies
 * as `Authorization: Bearer`) and Realtime (RLS scopes the change feed to the
 * signed-in user's projects). The anon key is public by design; it grants
 * nothing without a signed-in session.
 *
 * `supabase` is null when VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY were not
 * provided at BUILD time (Vite inlines them) - the UI then shows a clear
 * configuration error instead of a login form that can never work.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Parse the Supabase redirect fragment once: after an OAuth round-trip Supabase
// returns the session in `#access_token=...` (and error codes in
// `?error=...`). detectSessionInUrl consumes the success case; surfacing the
// error codes is on us.
export function oauthErrorFromUrl(): string | null {
  const params = new URLSearchParams(window.location.search);
  const err = params.get("error_description") ?? params.get("error");
  if (!err) return null;
  // Clean the address bar so a refresh doesn't re-show it.
  window.history.replaceState({}, "", window.location.pathname);
  return err;
}

const url = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.trim();
const anonKey = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined)?.trim();

export const authConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient | null = authConfigured
  ? createClient(url as string, anonKey as string, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      realtime: { params: { eventsPerSecond: 5 } },
    })
  : null;

/** Current access token (auto-refreshed by supabase-js when near expiry), or null. */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

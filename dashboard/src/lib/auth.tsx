/**
 * Auth context — the single owner of the signed-in identity.
 *
 * undefined = session still restoring (render nothing yet, no flash of the
 * login screen); null = signed out. The engine-visible credential is the
 * Supabase JWT; the dashboard's own view of the user comes from the engine's
 * verified GET /auth/me (memberships + role drive which controls render).
 * An engine 401 (expired/revoked session) signs the user out with a notice.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { ApiError, fetchMe, onUnauthorized, type Me } from "./api";
import { oauthErrorFromUrl, supabase } from "./supabase";

interface AuthContextValue {
  /** undefined = restoring, null = signed out, Session = signed in. */
  session: Session | null | undefined;
  me: Me | null;
  meError: string | null;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  session: undefined,
  me: null,
  meError: null,
  signOut: async () => undefined,
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) {
      setSession(null);
      return;
    }
    void supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data: sub } = supabase.auth.onAuthStateChange((_evt, s) => setSession(s));
    const client = supabase;
    onUnauthorized(() => {
      setAuthNotice("Your session expired or was rejected by the engine — please sign in again.");
      void client.auth.signOut();
    });
    return () => {
      sub.subscription.unsubscribe();
      onUnauthorized(null);
    };
  }, []);

  const userId = session?.user.id ?? null;
  useEffect(() => {
    if (!userId) {
      setMe(null);
      setMeError(null);
      return;
    }
    setMeError(null);
    setAuthNotice(null);
    fetchMe()
      .then(setMe)
      .catch((err) => {
        if (!(err instanceof ApiError && err.status === 401)) {
          setMeError(`Signed in, but the engine rejected the session: ${err instanceof Error ? err.message : err}`);
        }
      });
  }, [userId]);

  // Surface OAuth failures that come back as ?error=... on the redirect.
  useEffect(() => {
    if (session === null) {
      const oauthErr = oauthErrorFromUrl();
      if (oauthErr) setAuthNotice(`GitHub sign-in failed: ${oauthErr}`);
    }
  }, [session]);

  const signOut = useCallback(async () => {
    await supabase?.auth.signOut();
  }, []);

  const value = useMemo(
    () => ({ session, me, meError: meError ?? authNotice, signOut }),
    [session, me, meError, authNotice, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

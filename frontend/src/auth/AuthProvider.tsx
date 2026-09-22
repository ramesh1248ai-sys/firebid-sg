import type { User } from "oidc-client-ts";
import { useCallback, useEffect, useMemo, useState } from "react";

import { userManager } from "@/auth/oidc";
import { AuthContext, type AuthState, type Session, toSession } from "@/auth/session";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");

  useEffect(() => {
    let cancelled = false;
    const apply = (user: User | null) => {
      if (cancelled) return;
      const live = user !== null && !user.expired;
      setSession(live ? toSession(user) : null);
      setStatus(live ? "signed-in" : "signed-out");
    };

    void userManager.getUser().then(apply);
    userManager.events.addUserLoaded(apply);
    userManager.events.addUserUnloaded(() => apply(null));
    // A renewal that fails leaves the tab with no valid token; treat that as signed out.
    userManager.events.addSilentRenewError(() => apply(null));
    return () => {
      cancelled = true;
      userManager.events.removeUserLoaded(apply);
    };
  }, []);

  const signIn = useCallback(async () => {
    await userManager.signinRedirect({ state: { returnTo: window.location.pathname } });
  }, []);

  const signOut = useCallback(async () => {
    await userManager.signoutRedirect();
  }, []);

  const value = useMemo(
    () => ({ session, status, signIn, signOut }),
    [session, status, signIn, signOut],
  );
  return <AuthContext value={value}>{children}</AuthContext>;
}

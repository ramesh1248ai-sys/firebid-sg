import type { User } from "oidc-client-ts";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
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
      if (live) void known(toSession(user));
    };
    // A person's roles are kept by the application and changed there (ADR-013): the token
    // says who they are, the application what they may do.
    const known = async (from: Session) => {
      try {
        const { data, error, response } = await api.GET("/me");
        if (cancelled) return;
        if (data) setSession({ ...from, roles: data.roles, userId: data.user_id });
        else if (response.status === 403)
          setSession({
            ...from,
            roles: [],
            refused: apiErrorMessage(error, "Your access has been switched off"),
          });
      } catch {
        // Not reachable: the token's roles stand, and the API still decides every request.
      }
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

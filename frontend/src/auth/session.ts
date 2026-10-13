import type { User } from "oidc-client-ts";
import { createContext, use } from "react";

export interface Session {
  name: string;
  username: string;
  /** The roles held in this application, once it has said; the token's until then. */
  roles: readonly string[];
  /** Who this application knows the person as, once it has said. */
  userId?: string;
  /** Why the application will not serve them (their access is switched off). */
  refused?: string;
}

export interface AuthState {
  session: Session | null;
  status: "loading" | "signed-in" | "signed-out";
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

/** Claims shaped like Microsoft Entra ID's, which the Keycloak dev realm also issues. */
interface Claims {
  name?: string;
  preferred_username?: string;
  upn?: string;
  email?: string;
  roles?: string[] | string;
}

export function toSession(user: User): Session {
  const claims = user.profile as Claims;
  const username = claims.preferred_username ?? claims.upn ?? claims.email ?? "";
  const roles = claims.roles ?? [];
  return {
    name: claims.name ?? username,
    username,
    roles: typeof roles === "string" ? [roles] : roles,
  };
}

export function useAuth(): AuthState {
  const state = use(AuthContext);
  if (!state) throw new Error("useAuth must be used inside AuthProvider");
  return state;
}

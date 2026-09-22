import { UserManager, WebStorageStateStore } from "oidc-client-ts";

/**
 * Single sign-on against the identity provider: Keycloak in development, Microsoft Entra ID in
 * staging and production (NFR-01). Authorization code flow with PKCE, so no secret ever reaches
 * the browser.
 *
 * The token is held in memory and in session storage only, so closing the tab ends the session
 * and nothing survives in local storage for another tab to read.
 */
const env = import.meta.env;

export const oidcSettings = {
  authority: env.VITE_OIDC_AUTHORITY ?? "http://localhost:8081/realms/firebid",
  client_id: env.VITE_OIDC_CLIENT_ID ?? "firebid-web",
  redirect_uri: new URL("/auth/callback", window.location.origin).toString(),
  post_logout_redirect_uri: window.location.origin,
  response_type: "code",
  scope: env.VITE_OIDC_SCOPE ?? "openid profile email",
  // Renew in the background while the tab is open, so long work is not interrupted.
  automaticSilentRenew: true,
  // Ask for a fresh token a minute before the old one expires.
  accessTokenExpiringNotificationTimeInSeconds: 60,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
};

export const userManager = new UserManager(oidcSettings);

/** The bearer token for the current session, or null when nobody is signed in. */
export async function accessToken(): Promise<string | null> {
  const user = await userManager.getUser();
  if (!user || user.expired) return null;
  return user.access_token;
}

import type { User } from "oidc-client-ts";
import { vi } from "vitest";

/**
 * Stands in for the identity provider in tests, so the pages can be exercised signed in or
 * signed out without a browser redirect. Registered globally in `setup.ts`.
 */
type Listener = (user: User | null) => void;

const userLoaded: Listener[] = [];
const userUnloaded: (() => void)[] = [];
let current: User | null = null;

export const signinRedirect = vi.fn(async () => undefined);
export const signoutRedirect = vi.fn(async () => undefined);
export const signinCallback = vi.fn(async () => current ?? undefined);

export function signedInAs(
  profile: { name: string; preferred_username: string; roles?: string[] } = {
    name: "Bree Tan",
    preferred_username: "bid.manager@firebid.test",
    roles: ["bid_manager"],
  },
): void {
  current = { profile, access_token: "test-token", expired: false } as unknown as User;
  for (const listener of userLoaded) listener(current);
}

export function signedOut(): void {
  current = null;
  for (const listener of userUnloaded) listener();
}

export function resetOidc(): void {
  current = null;
  userLoaded.length = 0;
  userUnloaded.length = 0;
  signinRedirect.mockClear();
  signoutRedirect.mockClear();
}

export const userManager = {
  getUser: () => Promise.resolve(current),
  signinRedirect,
  signoutRedirect,
  signinCallback,
  events: {
    addUserLoaded: (listener: Listener) => userLoaded.push(listener),
    removeUserLoaded: (listener: Listener) => {
      const at = userLoaded.indexOf(listener);
      if (at >= 0) userLoaded.splice(at, 1);
    },
    addUserUnloaded: (listener: () => void) => userUnloaded.push(listener),
    addSilentRenewError: (listener: () => void) => userUnloaded.push(listener),
  },
};

export function accessToken(): Promise<string | null> {
  return Promise.resolve(current ? "test-token" : null);
}

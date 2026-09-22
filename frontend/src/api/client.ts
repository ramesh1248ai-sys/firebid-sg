import createClient, { type Middleware } from "openapi-fetch";

import { accessToken } from "@/auth/oidc";
import type { paths } from "./schema";

/**
 * Typed API client generated from the backend's OpenAPI schema (`make api-client`).
 * The API is served under /api on the app's own origin (Vite proxy in dev, nginx in the stack).
 */
export const api = createClient<paths>({
  baseUrl: new URL("/api", window.location.origin).toString(),
  // Resolve fetch at call time (not import time), so tests and instrumentation can wrap it.
  fetch: (request) => globalThis.fetch(request),
});

/** Every request carries the signed-in person's token; the API decides what they may see. */
const authorization: Middleware = {
  async onRequest({ request }) {
    const token = await accessToken();
    if (token) request.headers.set("authorization", `Bearer ${token}`);
    return request;
  },
};

api.use(authorization);

/** The API's error bodies are `{"detail": "..."}`; fall back to the status when they are not. */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Every test runs against the fake identity provider; each one says who is signed in.
vi.mock("@/auth/oidc", () => import("./fake-oidc"));

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  const { resetOidc } = await import("./fake-oidc");
  resetOidc();
});

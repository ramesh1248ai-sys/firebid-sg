import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// A file's first render loads the whole app; with every file running at once that can take
// longer than the default second a `findBy` waits.
configure({ asyncUtilTimeout: 4000 });

// Every test runs against the fake identity provider; each one says who is signed in.
vi.mock("@/auth/oidc", () => import("./fake-oidc"));

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  const { resetOidc } = await import("./fake-oidc");
  resetOidc();
});

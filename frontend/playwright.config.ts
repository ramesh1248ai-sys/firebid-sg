import { defineConfig, devices } from "@playwright/test";

// Runs against the container stack (make up). Inside the Dev Container the stack is reached
// through host.docker.internal, set via FIREBID_STACK_HOST.
const host = process.env.FIREBID_STACK_HOST ?? "localhost";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? `http://${host}:8080`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

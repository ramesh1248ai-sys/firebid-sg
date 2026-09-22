import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { routes } from "@/app/routes";

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function stubApi(response: Response) {
  vi.stubGlobal("fetch", vi.fn(async () => response));
}

describe("app shell", () => {
  it("shows the API version when the API responds", async () => {
    stubApi(Response.json({ version: "0.1.0", git_sha: "abc", env: "dev" }));
    renderAt("/");
    expect(screen.getByRole("heading", { name: "FireBid SG" })).toBeInTheDocument();
    expect(await screen.findByText(/API connected · version 0\.1\.0 \(dev\)/)).toBeInTheDocument();
  });

  it("says so when the API is unreachable", async () => {
    stubApi(new Response("down", { status: 503 }));
    renderAt("/");
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
  });

  it("navigates to the sign-in placeholder", async () => {
    stubApi(Response.json({ version: "0.1.0", git_sha: "abc", env: "dev" }));
    renderAt("/");
    await userEvent.click(screen.getByRole("link", { name: "Sign in" }));
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in with Microsoft" })).toBeDisabled();
  });
});

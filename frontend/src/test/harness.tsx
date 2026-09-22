import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { routes } from "@/app/routes";
import { AuthProvider } from "@/auth/AuthProvider";

/** Render the real routes at a path, with the real providers around them. */
export function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </AuthProvider>,
  );
}

export interface Call {
  url: string;
  method: string;
  authorization: string | null;
  body: string | null;
}

/**
 * Answer requests by path, and record what was asked. Keys are matched as substrings of the
 * URL, longest first, so "/bids/" wins over "/bids".
 */
export function stubApi(routeTable: Record<string, () => Response>): Call[] {
  const calls: Call[] = [];
  const keys = Object.keys(routeTable).sort((a, b) => b.length - a.length);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push({
        url: request.url,
        method: request.method,
        authorization: request.headers.get("authorization"),
        body: request.body ? await request.clone().text() : null,
      });
      const key = keys.find((candidate) => new URL(request.url).pathname.includes(candidate));
      const handler = key === undefined ? undefined : routeTable[key];
      if (!handler) return new Response("no stub", { status: 404 });
      return handler();
    }),
  );
  return calls;
}

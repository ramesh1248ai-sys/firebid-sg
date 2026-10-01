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

/** What was asked, from either shape of `fetch`, without touching a multipart body. */
async function describe(input: Request | URL | string, init?: RequestInit): Promise<Call> {
  if (input instanceof Request) {
    return {
      url: input.url,
      method: input.method,
      authorization: input.headers.get("authorization"),
      body: input.body ? await input.clone().text() : null,
    };
  }
  const headers = new Headers(init?.headers);
  const body = init?.body;
  return {
    url: new URL(input, window.location.origin).toString(),
    method: init?.method ?? "GET",
    authorization: headers.get("authorization"),
    // A FormData body is not readable as text under jsdom, and no test needs it.
    body: typeof body === "string" ? body : body ? "<multipart body>" : null,
  };
}

/** Each handler may look at the call it answers (its method, say). */
export function stubApi(routeTable: Record<string, (call: Call) => Response>): Call[] {
  const calls: Call[] = [];
  const keys = Object.keys(routeTable).sort((a, b) => b.length - a.length);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | URL | string, init?: RequestInit) => {
      const call = await describe(input, init);
      calls.push(call);
      const key = keys.find((candidate) => new URL(call.url).pathname.includes(candidate));
      const handler = key === undefined ? undefined : routeTable[key];
      if (!handler) return new Response("no stub", { status: 404 });
      return handler(call);
    }),
  );
  return calls;
}

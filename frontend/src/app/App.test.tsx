import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs, signinRedirect } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

function bid(overrides: Record<string, unknown> = {}) {
  return {
    id: "6a1f0c3e-0000-4000-8000-000000000001",
    human_id: "BID-2026-014",
    client_name: "Main Contractor Pte Ltd",
    tender_reference: "MC/2026/FP/014",
    state: "registered",
    stage: "S0",
    submission_deadline: "2026-10-01T09:00:00Z",
    clarification_cutoff: null,
    tender_validity_days: null,
    open_tasks: 2,
    overdue_tasks: 0,
    gates_passed: [],
    days_to_submission: 5,
    days_to_clarification_cutoff: null,
    missing_mandatory_fields: ["clarification_cutoff", "tender_validity_days"],
    ...overrides,
  };
}

describe("signing in", () => {
  it("sends someone signed out to the sign-in page", async () => {
    stubApi({});
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("goes to the identity provider when asked", async () => {
    stubApi({});
    renderAt("/sign-in");
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(signinRedirect).toHaveBeenCalled();
  });

  it("shows the signed-in person and the main navigation", async () => {
    signedInAs();
    stubApi({ "/bids": () => Response.json([]) });
    renderAt("/");
    expect(await screen.findByText("Bree Tan")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
  });
});

describe("the dashboard", () => {
  beforeEach(() => signedInAs());

  it("lists the bids with their deadline and what is still missing", async () => {
    stubApi({ "/bids": () => Response.json([bid()]) });
    renderAt("/");

    const row = within(await screen.findByRole("row", { name: /BID-2026-014/ }));
    expect(row.getByText(/01 Oct 2026 · 5 days left/)).toBeInTheDocument();
    expect(row.getByText(/Missing: clarification cutoff, tender validity days/)).toBeInTheDocument();
    expect(row.getByText(/2 open/)).toBeInTheDocument();
  });

  it("marks an overdue submission", async () => {
    stubApi({ "/bids": () => Response.json([bid({ days_to_submission: -1 })]) });
    renderAt("/");
    expect(await screen.findByText(/01 Oct 2026 · overdue/)).toBeInTheDocument();
  });

  it("carries the signed-in person's token on every request", async () => {
    const calls = stubApi({ "/bids": () => Response.json([]) });
    renderAt("/");
    await screen.findByText(/not on any bids yet/);
    expect(calls[0]?.authorization).toBe("Bearer test-token");
  });

  it("says so when the bids cannot be loaded", async () => {
    stubApi({ "/bids": () => new Response("down", { status: 503 }) });
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your bids");
  });
});

describe("registering a bid", () => {
  beforeEach(() => signedInAs());

  it("sends what was filled in and opens the new bid", async () => {
    const calls = stubApi({
      "/bids/": () => Response.json(bid()),
      "/bids": () => Response.json(bid()),
    });
    renderAt("/bids/new");

    await userEvent.type(await screen.findByLabelText("Project"), "Marina Tower");
    await userEvent.type(screen.getByLabelText("Client"), "Main Contractor Pte Ltd");
    await userEvent.type(screen.getByLabelText("Tender reference"), "MC/2026/FP/014");
    await userEvent.type(screen.getByLabelText("Submission deadline"), "2026-10-01T09:00");
    await userEvent.click(screen.getByRole("button", { name: "Register bid" }));

    const posted = calls.find((call) => call.method === "POST");
    expect(posted).toBeDefined();
    const body = JSON.parse(posted?.body ?? "{}") as Record<string, unknown>;
    expect(body.project_name).toBe("Marina Tower");
    expect(body.tender_reference).toBe("MC/2026/FP/014");
    expect(body.submission_deadline).toMatch(/^2026-10-01T/);
    expect(body.tender_validity_days).toBe(90);

    expect(await screen.findByRole("heading", { name: "BID-2026-014" })).toBeInTheDocument();
  });

  it("shows the reason the API refused", async () => {
    stubApi({
      "/bids": () => Response.json({ detail: "that tender reference is already registered" }, { status: 409 }),
    });
    renderAt("/bids/new");

    await userEvent.type(await screen.findByLabelText("Project"), "Marina Tower");
    await userEvent.type(screen.getByLabelText("Client"), "Main Contractor Pte Ltd");
    await userEvent.type(screen.getByLabelText("Tender reference"), "MC/2026/FP/014");
    await userEvent.type(screen.getByLabelText("Submission deadline"), "2026-10-01T09:00");
    await userEvent.click(screen.getByRole("button", { name: "Register bid" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "that tender reference is already registered",
    );
  });
});

describe("a bid the caller is not on", () => {
  it("is reported as not found, not as an error", async () => {
    signedInAs();
    stubApi({ "/bids/": () => Response.json({ detail: "bid not found" }, { status: 404 }) });
    renderAt("/bids/6a1f0c3e-0000-4000-8000-000000000009");
    expect(await screen.findByRole("heading", { name: "Bid not found" })).toBeInTheDocument();
  });
});

describe("the history", () => {
  beforeEach(() => signedInAs());

  const event = {
    id: "9c2f0c3e-0000-4000-8000-000000000001",
    occurred_at: "2026-09-20T02:30:00Z",
    actor_label: "Bree Tan",
    actor_id: null,
    action: "bid lifecycle: start qualification",
    entity_type: "bid",
    entity_id: "6a1f0c3e-0000-4000-8000-000000000001",
    bid_id: "6a1f0c3e-0000-4000-8000-000000000001",
    reason: "tender received",
    before: null,
    after: null,
  };

  it("shows what happened, newest first", async () => {
    stubApi({ "/audit": () => Response.json({ items: [event], next_cursor: null }) });
    renderAt("/audit");
    expect(await screen.findByText("bid lifecycle: start qualification")).toBeInTheDocument();
    expect(screen.getByText("tender received")).toBeInTheDocument();
  });

  it("asks the API only for the filters that were filled in", async () => {
    const calls = stubApi({ "/audit": () => Response.json({ items: [], next_cursor: null }) });
    renderAt("/audit");

    await userEvent.type(await screen.findByLabelText("Action contains"), "qualification");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => {
      const query = new URL(calls.at(-1)?.url ?? "").searchParams;
      expect(query.get("action")).toBe("qualification");
      expect(query.has("entity_type")).toBe(false);
    });
  });

  it("offers the filtered rows as a CSV", async () => {
    stubApi({ "/audit": () => Response.json({ items: [], next_cursor: null }) });
    renderAt("/audit");
    const link = await screen.findByRole("link", { name: "Export CSV" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/api/audit/export.csv"));
  });
});

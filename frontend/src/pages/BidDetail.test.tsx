import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * The bid's own page. A bid may be registered without its clarification cut-off and tender
 * validity; qualification waits on them, so they are set here, where the page says they
 * are missing.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";

function bid(overrides: Record<string, unknown> = {}) {
  return {
    id: BID,
    human_id: "BID-2026-014",
    project_id: "7b2f0c3e-0000-4000-8000-000000000001",
    client_name: "Main Contractor Pte Ltd",
    tender_reference: "MC/2026/FP/014",
    consultant: null,
    state: "registered",
    stage: "S0",
    submission_deadline: "2026-11-01T09:00:00Z",
    clarification_cutoff: null,
    tender_validity_days: null,
    missing_mandatory_fields: ["clarification_cutoff", "tender_validity_days"],
    ...overrides,
  };
}

const ESTHER = "9c2f0c3e-0000-4000-8000-000000000002";

function move(overrides: Record<string, unknown> = {}) {
  return {
    target: "qualifying",
    action: "start qualification",
    roles: ["bid_manager", "commercial_director", "senior_estimator"],
    permitted: true,
    refusal: null,
    ...overrides,
  };
}

/** The bid, with nobody to add and nothing to move unless a test says otherwise. */
function stubs(
  handler: (call: Call) => Response,
  extra: Record<string, (call: Call) => Response> = {},
) {
  return stubApi({
    [`/bids/${BID}/members/candidates`]: () => Response.json([]),
    [`/bids/${BID}/members`]: () => Response.json([]),
    [`/bids/${BID}/transitions`]: () => Response.json([]),
    ...extra,
    [`/bids/${BID}`]: handler,
  });
}

describe("a bid's page", () => {
  beforeEach(() => signedInAs());

  it("says what qualification waits on, and offers to set it", async () => {
    stubs(() => Response.json(bid()));
    renderAt(`/bids/${BID}`);

    expect(await screen.findByRole("heading", { name: "BID-2026-014" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Qualification is blocked until these are in: clarification cutoff, tender validity days",
    );
    expect(screen.getByRole("button", { name: "Set clarifications close" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Set tender validity" })).toBeVisible();
  });

  it("sets the tender validity in place", async () => {
    const calls = stubs((call) =>
      call.method === "PATCH"
        ? Response.json(
            bid({ tender_validity_days: 90, missing_mandatory_fields: ["clarification_cutoff"] }),
          )
        : Response.json(bid()),
    );
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Set tender validity" }));
    await userEvent.type(screen.getByLabelText("Tender validity"), "90");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("90 days")).toBeInTheDocument();
    const patch = calls.find((call) => call.method === "PATCH");
    expect(JSON.parse(patch!.body!)).toEqual({ tender_validity_days: 90 });
    expect(screen.getByRole("button", { name: "Change tender validity" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Qualification is blocked until these are in: clarification cutoff",
    );
  });

  it("sets when clarifications close, as an instant", async () => {
    const calls = stubs((call) =>
      call.method === "PATCH"
        ? Response.json(bid({ clarification_cutoff: "2026-10-20T09:00:00Z" }))
        : Response.json(bid()),
    );
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Set clarifications close" }));
    await userEvent.type(screen.getByLabelText("Clarifications close"), "2026-10-20T17:00");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("20 Oct 2026")).toBeInTheDocument();
    const patch = calls.find((call) => call.method === "PATCH");
    expect(JSON.parse(patch!.body!)).toEqual({
      clarification_cutoff: new Date("2026-10-20T17:00").toISOString(),
    });
  });

  it("shows why a change was refused, and keeps the form open", async () => {
    stubs((call) =>
      call.method === "PATCH"
        ? Response.json({ detail: "a submitted bid is frozen" }, { status: 409 })
        : Response.json(bid({ tender_validity_days: 60, missing_mandatory_fields: [] })),
    );
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Change tender validity" }));
    const field = screen.getByLabelText("Tender validity");
    expect(field).toHaveValue(60);
    await userEvent.clear(field);
    await userEvent.type(field, "120");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("a submitted bid is frozen");
    expect(screen.getByLabelText("Tender validity")).toHaveValue(120);
  });

  it("does not offer a change on a submitted bid", async () => {
    stubs(() =>
      Response.json(
        bid({ state: "submitted", tender_validity_days: 90, missing_mandatory_fields: [] }),
      ),
    );
    renderAt(`/bids/${BID}`);

    const details = (await screen.findByText("90 days")).closest("dl")!;
    expect(within(details).queryByRole("button")).toBeNull();
  });
});

describe("the team of a bid", () => {
  const people = [
    {
      user_id: "5db3d143-0000-4000-8000-000000000001",
      role: "bid_manager",
      display_name: "Bree Tan",
    },
  ];
  const candidates = [
    {
      user_id: ESTHER,
      display_name: "Esther Tan",
      username: "estimator@firebid.test",
      roles: ["estimator"],
    },
  ];

  // req: FR-BID-01
  it("shows who is on the bid, and lets a bid manager add someone in a role", async () => {
    signedInAs();
    const calls = stubs(() => Response.json(bid()), {
      [`/bids/${BID}/members/candidates`]: () => Response.json(candidates),
      [`/bids/${BID}/members`]: (call) =>
        call.method === "POST"
          ? Response.json(
              { user_id: ESTHER, role: "estimator", display_name: "Esther Tan" },
              { status: 201 },
            )
          : Response.json(people),
    });
    renderAt(`/bids/${BID}`);

    const team = await screen.findByRole("list", { name: "People on this bid" });
    expect(team).toHaveTextContent("Bree Tan");
    expect(team).toHaveTextContent("bid manager");

    await screen.findByRole("option", { name: "Esther Tan (estimator@firebid.test)" });
    await userEvent.selectOptions(
      screen.getByLabelText("Person to add"),
      "Esther Tan (estimator@firebid.test)",
    );
    // The role she holds in the organisation is offered, and can be changed.
    expect(screen.getByLabelText("Role on this bid")).toHaveValue("estimator");
    await userEvent.selectOptions(screen.getByLabelText("Role on this bid"), "senior estimator");
    await userEvent.click(screen.getByRole("button", { name: "Add to the team" }));

    const post = await vi.waitFor(() => {
      const found = calls.find((call) => call.method === "POST");
      expect(found).toBeDefined();
      return found!;
    });
    expect(post.url).toContain(`/bids/${BID}/members`);
    expect(JSON.parse(post.body!)).toEqual({ user_id: ESTHER, role: "senior_estimator" });
  });

  // req: FR-BID-01
  it("lets a bid manager change a role, and take someone off the bid after asking", async () => {
    signedInAs();
    const both = [
      ...people,
      { user_id: ESTHER, role: "estimator", display_name: "Esther Tan" },
    ];
    const calls = stubs(() => Response.json(bid()), {
      [`/bids/${BID}/members/${ESTHER}`]: (call) =>
        call.method === "DELETE"
          ? new Response(null, { status: 204 })
          : Response.json({ user_id: ESTHER, role: "senior_estimator", display_name: "Esther Tan" }),
      [`/bids/${BID}/members`]: () => Response.json(both),
    });
    renderAt(`/bids/${BID}`);

    await userEvent.selectOptions(await screen.findByLabelText("Role of Esther Tan"), "senior estimator");
    const patch = await vi.waitFor(() => {
      const found = calls.find((call) => call.method === "PATCH");
      expect(found).toBeDefined();
      return found!;
    });
    expect(patch.url).toContain(`/bids/${BID}/members/${ESTHER}`);
    expect(JSON.parse(patch.body!)).toEqual({ role: "senior_estimator" });

    // One press asks; nothing is sent until the second.
    await userEvent.click(screen.getByRole("button", { name: "Take Esther Tan off the bid" }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Yes, take Esther Tan off" }));
    await vi.waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  // req: FR-ADM-01
  it("says why the last bid manager cannot be taken off", async () => {
    signedInAs();
    stubs(() => Response.json(bid()), {
      [`/bids/${BID}/members/${people[0]!.user_id}`]: () =>
        Response.json(
          { detail: "a bid keeps at least one bid manager: add another before changing this one" },
          { status: 409 },
        ),
      [`/bids/${BID}/members`]: () => Response.json(people),
    });
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Take Bree Tan off the bid" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, take Bree Tan off" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("a bid keeps at least one bid manager");
  });

  // req: FR-ADM-01
  it("shows the team to an estimator and offers them nobody to add", async () => {
    signedInAs({
      name: "Esther Tan",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    });
    const calls = stubs(() => Response.json(bid()), {
      [`/bids/${BID}/members`]: () => Response.json(people),
    });
    renderAt(`/bids/${BID}`);

    expect(await screen.findByRole("list", { name: "People on this bid" })).toHaveTextContent(
      "Bree Tan",
    );
    expect(screen.queryByLabelText("Person to add")).toBeNull();
    expect(screen.queryByLabelText("Role of Bree Tan")).toBeNull();
    expect(screen.queryByRole("button", { name: /off the bid/ })).toBeNull();
    expect(calls.some((call) => call.url.includes("/members/candidates"))).toBe(false);
  });
});

describe("moving a bid on", () => {
  beforeEach(() => signedInAs());

  // req: FR-BID-01
  it("offers a move that is open to the reader, and moves the bid", async () => {
    const calls = stubs(() => Response.json(bid({ missing_mandatory_fields: [] })), {
      [`/bids/${BID}/transitions`]: (call) =>
        call.method === "POST"
          ? Response.json(bid({ state: "qualifying", missing_mandatory_fields: [] }))
          : Response.json([move(), move({ target: "withdrawn", action: "withdraw" })]),
    });
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Start qualification" }));

    expect(await screen.findByText("qualifying")).toBeInTheDocument();
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!)).toEqual({ target: "qualifying", reason: null });
  });

  // req: FR-BID-01
  it("says what a move waits on, and whose a move is, and offers neither", async () => {
    stubs(() => Response.json(bid()), {
      [`/bids/${BID}/transitions`]: () =>
        Response.json([
          move({ refusal: "these details are missing: clarification_cutoff" }),
          move({
            target: "in_preparation",
            action: "bid (G0)",
            roles: ["commercial_director"],
            permitted: false,
          }),
        ]),
    });
    renderAt(`/bids/${BID}`);

    const moves = await screen.findByRole("list", { name: "Moves" });
    expect(within(moves).getByRole("button", { name: "Start qualification" })).toBeDisabled();
    expect(moves).toHaveTextContent("Waits on: these details are missing: clarification cutoff.");
    expect(within(moves).getByRole("button", { name: "Bid (G0)" })).toBeDisabled();
    expect(moves).toHaveTextContent("For the commercial director.");
  });

  // req: FR-BID-01
  it("withdraws a bid only with a reason, and sends it", async () => {
    const calls = stubs(() => Response.json(bid()), {
      [`/bids/${BID}/transitions`]: (call) =>
        call.method === "POST"
          ? Response.json(bid({ state: "withdrawn" }))
          : Response.json([move({ target: "withdrawn", action: "withdraw" })]),
    });
    renderAt(`/bids/${BID}`);

    const withdraw = await screen.findByRole("button", { name: "Withdraw" });
    expect(withdraw).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason for the move"), "the client cancelled");
    await userEvent.click(withdraw);

    await screen.findByText("withdrawn");
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!)).toEqual({
      target: "withdrawn",
      reason: "the client cancelled",
    });
  });

  // req: FR-BID-01
  it("shows why the API refused a move", async () => {
    stubs(() => Response.json(bid()), {
      [`/bids/${BID}/transitions`]: (call) =>
        call.method === "POST"
          ? Response.json({ detail: "bid lifecycle: the team is incomplete" }, { status: 409 })
          : Response.json([move()]),
    });
    renderAt(`/bids/${BID}`);

    await userEvent.click(await screen.findByRole("button", { name: "Start qualification" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("the team is incomplete");
    expect(screen.getByText("registered")).toBeInTheDocument();
  });
});

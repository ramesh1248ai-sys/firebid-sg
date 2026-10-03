import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

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

function stubs(handler: (call: Call) => Response) {
  return stubApi({ [`/bids/${BID}`]: handler });
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

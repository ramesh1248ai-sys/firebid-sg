import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * The review page. What matters: the pack shows the estimate with each section one click
 * from its source; a gate shows who approved it and on which hash, or why it is blocked;
 * only the Commercial Director is offered G3 and G4; the frozen submission says whether it
 * verifies and offers its files; the outcome is recorded.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const HASH = "a".repeat(64);

const PACK = {
  human_id: "BID-2026-014",
  client: "Main Contractor Pte Ltd",
  reference: "MC/2026/FP/014",
  generated_at: "2026-10-04T08:00:00Z",
  priced_on: "2026-10-04",
  figures: {
    direct: "100000.00",
    total_excluding_gst: "118800.00",
    gst: "10692.00",
    total_including_gst: "129492.00",
    margin: "8800.00",
    priced_bill: "82000.00",
    labour_hours: "640.00",
    labour_cost: "18000.00",
    risk_allowances: "5000.00",
    unpriced_lines: "2",
    open_clarifications: "1",
    open_issues: "3",
    items_verified_percent: "100.0",
  },
  figure_labels: {
    total_excluding_gst: "Total excluding GST (SGD)",
    gst: "GST (SGD)",
    total_including_gst: "Total including GST (SGD)",
    margin: "Margin (SGD)",
    risk_allowances: "Risk allowances (SGD)",
    unpriced_lines: "Unpriced lines",
  },
  sections: [
    {
      key: "estimate_by_component",
      title: "Estimate by component",
      link: `/bids/${BID}/boq`,
      columns: ["Component", "Basis", "Source", "Amount (SGD)"],
      rows: [
        ["Materials", "calculated", "the priced bill of quantities", "82,000.00"],
        ["Total (excluding GST)", "", "", "118,800.00"],
      ],
      note: "Priced on 2026-10-04.",
    },
    {
      key: "risk_allowances",
      title: "Risk allowances and treatments",
      link: `/bids/${BID}/risk`,
      columns: ["Risk", "Treatment", "Allowance (SGD)"],
      rows: [["Night work", "price", "5,000.00"]],
      note: "Allowances total SGD 5,000.00.",
    },
  ],
};

function gate(name: string, overrides: Record<string, unknown> = {}) {
  return {
    gate: name,
    role: name === "G1" || name === "G2" ? "senior_estimator" : "commercial_director",
    approved: false,
    blockers: [],
    approver_role: null,
    approver_id: null,
    decided_at: null,
    comment: null,
    snapshot_hash: null,
    ...overrides,
  };
}

const APPROVED = { approved: true, decided_at: "2026-10-03T09:00:00Z", snapshot_hash: HASH };

function stubs(
  gates: unknown[],
  extra: Record<string, (call: Call) => Response> = {},
  state = "under_review",
) {
  return stubApi({
    "/review-pack": () => Response.json(PACK),
    "/gates": () => Response.json({ state, gates }),
    "/submission": () => Response.json(null),
    "/outcome": () => Response.json(null),
    ...extra,
  });
}

const director = () =>
  signedInAs({
    name: "Carl Koh",
    preferred_username: "commercial.director@firebid.test",
    roles: ["commercial_director"],
  });

describe("the review page", () => {
  beforeEach(director);

  // req: FR-PKG-01
  it("shows the estimate with each section one click from where it comes from", async () => {
    stubs([gate("G1", APPROVED), gate("G2"), gate("G3"), gate("G4")]);
    renderAt(`/bids/${BID}/review`);

    const figures = await screen.findByLabelText("Headline figures");
    expect(figures).toHaveTextContent("Total excluding GST (SGD)118800.00");
    expect(figures).toHaveTextContent("Margin (SGD)8800.00");
    const components = screen.getByRole("table", { name: "Estimate by component" });
    expect(within(components).getByText("Materials").closest("tr")).toHaveTextContent("82,000.00");
    const links = screen.getAllByRole("link", { name: "Open in the platform" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/bids/${BID}/boq`,
      `/bids/${BID}/risk`,
    ]);
    expect(screen.getByRole("button", { name: /Review pack \(PDF\)/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Review pack \(Excel\)/ })).toBeVisible();
  });

  // req: FR-PKG-02
  it("shows why a gate is blocked, and does not offer its approval", async () => {
    stubs([
      gate("G1", APPROVED),
      gate("G2", { ...APPROVED, comment: "as measured" }),
      gate("G3", { blockers: ["2 risk(s) with no treatment"] }),
      gate("G4", { blockers: ["G3 is not approved"] }),
    ]);
    renderAt(`/bids/${BID}/review`);

    const list = await screen.findByRole("list", { name: "Gates" });
    const [g1, g2, g3] = within(list).getAllByRole("listitem");
    expect(g1).toHaveAttribute("data-approved", "yes");
    expect(g2).toHaveTextContent("on aaaaaaaaaaaaaaaa…");
    expect(g2).toHaveTextContent("as measured");
    expect(g3).toHaveTextContent("blocked");
    expect(g3).toHaveTextContent("2 risk(s) with no treatment");
    expect(screen.getByRole("button", { name: "Approve G3" })).toBeDisabled();
  });

  // req: FR-PKG-02
  it("lets the Commercial Director approve a gate that is ready, with a comment", async () => {
    const calls = stubs(
      [gate("G1", APPROVED), gate("G2", APPROVED), gate("G3"), gate("G4", { blockers: ["G3 is not approved"] })],
      { "/gates/g3/approve": () => Response.json({ state: "approved_for_submission", gates: [] }, { status: 201 }) },
    );
    renderAt(`/bids/${BID}/review`);

    await userEvent.type(await screen.findByLabelText("Approval comment"), "margin agreed");
    await userEvent.click(screen.getByRole("button", { name: "Approve G3" }));

    const post = calls.find((call) => call.method === "POST");
    expect(post!.url).toContain("/gates/g3/approve");
    expect(JSON.parse(post!.body!)).toEqual({ comment: "margin agreed" });
    expect(screen.getByText(/The platform sends nothing/)).toBeVisible();
  });

  // req: FR-PKG-02
  it("lets the Senior Estimator approve G2, and offers them no other gate", async () => {
    signedInAs({
      name: "Sam Lim",
      preferred_username: "senior.estimator@firebid.test",
      roles: ["senior_estimator"],
    });
    const calls = stubs([gate("G1", APPROVED), gate("G2"), gate("G3"), gate("G4")], {
      "/boq/g2/approve": () =>
        Response.json({ id: "x", gate: "G2", decision: "approved" }, { status: 201 }),
    });
    renderAt(`/bids/${BID}/review`);

    await userEvent.type(await screen.findByLabelText("Approval comment"), "bill reconciled");
    await userEvent.click(screen.getByRole("button", { name: "Approve G2" }));

    const post = calls.find((call) => call.method === "POST");
    expect(post!.url).toContain("/boq/g2/approve");
    expect(JSON.parse(post!.body!)).toEqual({ comment: "bill reconciled" });
    expect(screen.queryByRole("button", { name: "Approve G3" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve G4/ })).toBeNull();
  });

  // req: FR-PKG-02
  it("keeps G2 from the Senior Estimator while something blocks it", async () => {
    signedInAs({
      name: "Sam Lim",
      preferred_username: "senior.estimator@firebid.test",
      roles: ["senior_estimator"],
    });
    stubs([
      gate("G1", APPROVED),
      gate("G2", { blockers: ["no BOQ is built"] }),
      gate("G3"),
      gate("G4"),
    ]);
    renderAt(`/bids/${BID}/review`);

    expect(await screen.findByRole("button", { name: "Approve G2" })).toBeDisabled();
    expect(screen.getByRole("list", { name: "Gates" })).toHaveTextContent("no BOQ is built");
  });

  // req: FR-PKG-02
  it("does not offer the gates to anyone else", async () => {
    signedInAs({
      name: "Bella Ong",
      preferred_username: "bid.manager@firebid.test",
      roles: ["bid_manager"],
    });
    stubs([gate("G1", APPROVED), gate("G2", APPROVED), gate("G3"), gate("G4")]);
    renderAt(`/bids/${BID}/review`);

    await screen.findByRole("list", { name: "Gates" });
    expect(screen.queryByRole("button", { name: "Approve G2" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve G3" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve G4/ })).toBeNull();
  });

  // req: FR-PKG-03
  it("says whether the frozen submission verifies, and offers its files", async () => {
    const calls = stubs(
      [gate("G1", APPROVED), gate("G2", APPROVED), gate("G3", APPROVED), gate("G4", APPROVED)],
      {
        "/submission": () =>
          Response.json({
            id: "bbbbbbbb-0000-4000-8000-000000000001",
            manifest_sha256: HASH,
            frozen_by: "Carl Koh",
            frozen_at: "2026-10-04T09:00:00Z",
            files: [
              { name: "company-boq.xlsx", sha256: "c".repeat(64), size: 20480, content_type: "x" },
            ],
            record_counts: { boq_line: 16 },
            verified: false,
            problems: ["company-boq.xlsx is not the file that was frozen: its hash differs"],
            changed_since: [],
          }),
        "/submission/files/company-boq.xlsx": () =>
          new Response(new Blob(["x"]), {
            headers: { "content-disposition": 'attachment; filename="BID company-boq.xlsx"' },
          }),
      },
      "submitted",
    );
    URL.createObjectURL = () => "blob:x";
    URL.revokeObjectURL = () => undefined;
    renderAt(`/bids/${BID}/review`);

    expect(await screen.findByRole("status", { name: "Snapshot verification" })).toHaveTextContent(
      "The snapshot does not verify: company-boq.xlsx is not the file that was frozen",
    );
    const files = screen.getByRole("list", { name: "Submission files" });
    expect(files).toHaveTextContent("company-boq.xlsx 20 KB");
    await userEvent.click(within(files).getByRole("button", { name: "Download company-boq.xlsx" }));

    const request = calls.find((call) => call.url.includes("/submission/files/"));
    expect(request!.method).toBe("GET");
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  // req: FR-LRN-02
  it("records the outcome with the price and the reasons", async () => {
    const calls = stubs(
      [gate("G1", APPROVED), gate("G2", APPROVED), gate("G3", APPROVED), gate("G4", APPROVED)],
      {
        "/submission": () =>
          Response.json({
            id: "bbbbbbbb-0000-4000-8000-000000000001",
            manifest_sha256: HASH,
            frozen_by: "Carl Koh",
            frozen_at: "2026-10-04T09:00:00Z",
            files: [],
            record_counts: {},
            verified: true,
            problems: [],
            changed_since: [],
          }),
        "/outcome": (call) =>
          call.method === "POST" ? Response.json({}) : Response.json(null),
      },
      "submitted",
    );
    renderAt(`/bids/${BID}/review`);

    const form = await screen.findByRole("form", { name: "Outcome" });
    await userEvent.selectOptions(within(form).getByLabelText("Result"), "awarded");
    await userEvent.type(within(form).getByLabelText("Awarded price"), "412500");
    // The text is kept for good: the form says so before anything is typed.
    expect(form).toHaveTextContent("Do not name individuals");
    await userEvent.type(within(form).getByLabelText("Reasons"), "Lowest compliant offer");
    await userEvent.click(within(form).getByRole("button", { name: "Record the outcome" }));

    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!)).toEqual({
      outcome: "awarded",
      awarded_price: "412500",
      reasons: "Lowest compliant offer",
      competitor_feedback: "",
    });
  });
});

describe("proposed library changes", () => {
  const PROPOSAL = {
    id: "dddddddd-0000-4000-8000-000000000001",
    library: "rate",
    payload: {
      item_key: "check_valve|150||||",
      unit: "no",
      unit_rate: "640.00",
      source_type: "purchase_order",
      source_reference: "PO-26-0455",
      description: "Check valve DN150, flanged",
      effective_from: "2026-10-01",
      valid_until: null,
    },
    source: "estimator",
    source_ref: null,
    reason: "the PO price from the Tampines job",
    state: "proposed",
    proposed_by: "Ethan Lim",
    decided_by: null,
    decided_at: null,
    note: null,
    applied_entry_id: null,
    created_at: "2026-10-04T08:00:00Z",
  };

  // req: FR-LRN-03
  it("shows a proposal as waiting, and lets only the senior estimator approve it", async () => {
    signedInAs({
      name: "Sam Senior",
      preferred_username: "senior.estimator@firebid.test",
      roles: ["senior_estimator"],
    });
    const calls = stubApi({
      "/rates": () => Response.json([]),
      "/library-proposals": () => Response.json([PROPOSAL]),
      [`/library-proposals/${PROPOSAL.id}/decide`]: () =>
        Response.json({ ...PROPOSAL, state: "approved" }),
    });
    renderAt("/rates");

    const list = await screen.findByRole("list", { name: "Library proposals" });
    const [item] = within(list).getAllByRole("listitem");
    expect(item).toHaveTextContent("proposed");
    expect(item).toHaveTextContent("Check valve DN150, flanged · check_valve|150|||| · SGD 640.00 per no");
    expect(item).toHaveTextContent("Why: the PO price from the Tampines job");
    expect(screen.getByText(/changes only when the senior estimator approves/)).toBeVisible();
    await userEvent.click(within(item!).getByRole("button", { name: "Approve" }));

    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!)).toEqual({ approve: true, note: null });
  });

  // req: FR-LRN-03
  it("offers an estimator the proposal form, and no approval", async () => {
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    });
    const calls = stubApi({
      "/rates": () => Response.json([]),
      "/library-proposals": (call) =>
        call.method === "POST" ? Response.json(PROPOSAL, { status: 201 }) : Response.json([PROPOSAL]),
    });
    renderAt("/rates");

    const list = await screen.findByRole("list", { name: "Library proposals" });
    expect(within(list).queryByRole("button", { name: "Approve" })).toBeNull();
    const form = screen.getByRole("form", { name: "Propose a rate" });
    const propose = within(form).getByRole("button", { name: "Propose" });
    expect(propose).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Description"), "Check valve DN150");
    await userEvent.type(within(form).getByLabelText("Rate (SGD)"), "640");
    await userEvent.type(
      within(form).getByLabelText("Why the library should change"),
      "the PO price",
    );
    await userEvent.click(propose);

    const post = calls.find((call) => call.method === "POST");
    const body = JSON.parse(post!.body!);
    expect(body).toMatchObject({ library: "rate", source: "estimator", reason: "the PO price" });
    expect(body.payload).toMatchObject({ description: "Check valve DN150", unit_rate: "640" });
  });
});

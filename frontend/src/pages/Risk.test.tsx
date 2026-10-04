import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * The risk page. What matters: the checklist shows what was proposed and from what, and an
 * unresolved item holds up G3; a risk shows its evidence, its treatment and what its impact
 * was computed from; an adjustment needs a reason; every qualification says where it is from.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const PUMPS = "11111111-0000-4000-8000-000000000001";
const NIGHT = "22222222-0000-4000-8000-000000000001";
const ENTRY = "33333333-0000-4000-8000-000000000001";

function check(overrides: Record<string, unknown> = {}) {
  return {
    id: PUMPS,
    system: "sprinkler",
    item_key: "pumps",
    label: "Fire pumps and jockey pumps",
    proposed_status: "open",
    status: "open",
    basis: "neither the scope matrix nor the takeoff settles it",
    evidence: [],
    decided_by: null,
    decided_at: null,
    note: null,
    ...overrides,
  };
}

const POWER = check({
  id: "11111111-0000-4000-8000-000000000002",
  item_key: "power_supply",
  label: "Power supply interfaces",
  proposed_status: "by_others",
  status: "by_others",
  basis: "the scope matrix has it by others",
  evidence: [{ kind: "clause", label: "Specification clause 7.1", quote: "by the electrical contractor" }],
});

function risk(overrides: Record<string, unknown> = {}) {
  return {
    id: NIGHT,
    key: "execution:night_work",
    category: "execution",
    kind: "night_work",
    title: "Night work",
    description: "Night work, as stated in Specification clause 8.4.",
    evidence: [
      {
        kind: "clause",
        label: "Specification clause 8.4",
        quote: "Works in the retail podium shall be carried out at night between 2200 and 0600 hours.",
      },
    ],
    level: null,
    proposed_treatment: "price",
    treatment: null,
    owner: null,
    status: "open",
    note: null,
    impact: {
      method: "labour_multiplier",
      basis:
        `"Night work" (x 1.20) on 120.00 man-hours over 3 bill line(s) on the whole bid: 24.00 man-hours more, SGD 480.00 at the trades' rates`,
      cost: "480.00",
      hours: "24.00",
      multiplier: "night_work",
      factor: "1.20",
    },
    impact_state: "computed",
    cost_allowance: "480.00",
    programme_hours: "24.00",
    impact_reason: null,
    impact_by: null,
    impact_at: null,
    ...overrides,
  };
}

const DESIGN = risk({
  id: "22222222-0000-4000-8000-000000000002",
  key: "design:design_and_build",
  category: "design_responsibility",
  kind: "design_and_build",
  title: "Design and build: the contractor's responsibility",
  description: "The specification (clause 8.1) puts design and build on the contractor.",
  evidence: [
    {
      kind: "clause",
      label: "Specification clause 8.1",
      quote: "The sprinkler installation shall be procured on a design and build basis.",
    },
  ],
  proposed_treatment: "qualify",
  impact: { method: "not_computed", basis: "design work is not priced by an engine" },
  cost_allowance: null,
  programme_hours: null,
});

const ENTRY_ROW = {
  id: ENTRY,
  kind: "exclusion",
  text: "Builder's works (sprinkler) is excluded from our offer.",
  source_kind: "scope_check",
  source_ref: "11111111-0000-4000-8000-000000000003",
  source_label: "Builder's works (sprinkler)",
  state: "proposed",
  decided_by: null,
  decided_at: null,
  note: null,
};

function page(overrides: Record<string, unknown> = {}) {
  return {
    checklist: [check(), POWER],
    risks: [risk(), DESIGN],
    qualifications: [ENTRY_ROW],
    g3: {
      ready: false,
      checklist_built: true,
      open_checks: [{ id: PUMPS }],
      untreated_risks: [{ id: NIGHT }, { id: DESIGN.id }],
      summary: "1 scope checklist item(s) not resolved; 2 risk(s) with no treatment",
    },
    statuses: ["included", "excluded", "by_others", "clarified"],
    treatments: ["price", "qualify", "clarify", "accept"],
    kinds: ["qualification", "assumption", "exclusion", "deviation"],
    ...overrides,
  };
}

function stubs(extra: Record<string, (call: Call) => Response> = {}, body = page()) {
  return stubApi({ "/risk": () => Response.json(body), ...extra });
}

describe("the risk page", () => {
  beforeEach(() =>
    signedInAs({
      name: "Bella Ong",
      preferred_username: "bid.manager@firebid.test",
      roles: ["bid_manager"],
    }),
  );

  // req: FR-RSK-01
  it("shows what each checklist item was proposed from, and holds G3 until it is resolved", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/risk`);

    expect(await screen.findByRole("status", { name: "G3 readiness" })).toHaveTextContent(
      "Not ready for G3: 1 scope checklist item(s) not resolved; 2 risk(s) with no treatment.",
    );
    const table = screen.getByRole("table", { name: "Checklist: sprinkler" });
    const power = within(table).getByText("Power supply interfaces").closest("tr")!;
    expect(power).toHaveTextContent("by others");
    expect(power).toHaveTextContent("the scope matrix has it by others");
    const pumps = within(table).getByText("Fire pumps and jockey pumps").closest("tr")!;
    expect(pumps).toHaveAttribute("data-status", "open");
    expect(pumps).toHaveTextContent("to resolve");
    const save = within(pumps).getByRole("button", { name: "Resolve Fire pumps and jockey pumps" });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(
      within(pumps).getByLabelText("Status of Fire pumps and jockey pumps"),
      "by_others",
    );
    await userEvent.type(
      within(pumps).getByLabelText("Note on Fire pumps and jockey pumps"),
      "main contract",
    );
    await userEvent.click(save);

    const put = calls.find((call) => call.method === "PUT");
    expect(put!.url).toContain(`/risk/checklist/${PUMPS}`);
    expect(JSON.parse(put!.body!)).toEqual({ status: "by_others", note: "main contract" });
  });

  // req: FR-RSK-01
  it("says so when everything is resolved and treated", async () => {
    stubs(
      {},
      page({
        g3: { ready: true, checklist_built: true, open_checks: [], untreated_risks: [], summary: "" },
      }),
    );
    renderAt(`/bids/${BID}/risk`);

    expect(await screen.findByRole("status", { name: "G3 readiness" })).toHaveTextContent(
      "Ready for G3",
    );
  });

  // req: FR-RSK-02
  it("shows a design-responsibility risk with the clause it cites and a proposed treatment", async () => {
    stubs();
    renderAt(`/bids/${BID}/risk`);

    const list = await screen.findByRole("list", { name: "Risk register" });
    const card = within(list).getByText(/Design and build/).closest("li")!;
    expect(card).toHaveTextContent("design responsibility");
    expect(card).toHaveTextContent("Specification clause 8.1");
    expect(card).toHaveTextContent("procured on a design and build basis");
    expect(within(card).getByLabelText(/^Treatment of Design and build/)).toHaveValue("qualify");
    expect(card).toHaveTextContent("Not computed: design work is not priced by an engine");
    expect(within(card).queryByRole("button", { name: "Accept the computed impact" })).toBeNull();
  });

  // req: FR-RSK-03
  it("shows an execution risk with its evidence", async () => {
    stubs();
    renderAt(`/bids/${BID}/risk`);

    const evidence = await screen.findByRole("list", { name: "Evidence for Night work" });
    expect(evidence).toHaveTextContent("Specification clause 8.4");
    expect(evidence).toHaveTextContent("at night between 2200 and 0600 hours");
  });

  // req: FR-RSK-04
  it("shows what the impact was computed from, and adjusts it only with a reason", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/risk`);

    const list = await screen.findByRole("list", { name: "Risk register" });
    const card = within(list).getByText("Night work").closest("li")!;
    expect(card).toHaveTextContent("SGD 480.00 · 24 man-hours");
    expect(card).toHaveTextContent('Computed: "Night work" (x 1.20) on 120.00 man-hours');
    const adjust = within(card).getByRole("button", { name: "Adjust the impact of Night work" });
    expect(adjust).toBeDisabled();
    await userEvent.type(within(card).getByLabelText("Allowance for Night work"), "5000");
    await userEvent.type(within(card).getByLabelText("Reason for Night work"), "podium only");
    await userEvent.click(adjust);
    await userEvent.click(within(card).getByRole("button", { name: "Accept the computed impact" }));

    const puts = calls.filter((call) => call.method === "PUT" && call.url.endsWith("/impact"));
    expect(JSON.parse(puts[0]!.body!)).toEqual({
      cost: "5000",
      hours: null,
      reason: "podium only",
      accept_computed: false,
    });
    expect(JSON.parse(puts[1]!.body!)).toEqual({ accept_computed: true });
  });

  // req: FR-RSK-05
  it("shows where every qualification comes from, and saves an edited wording", async () => {
    const calls = stubs({
      [`/qualifications/${ENTRY}/history`]: () =>
        Response.json([
          {
            at: "2026-10-04T08:00:00Z",
            by: "Bella Ong",
            action: "qualification: edited",
            before: { kind: "exclusion" },
            after: { kind: "exclusion" },
            reason: "as agreed",
          },
        ]),
    });
    renderAt(`/bids/${BID}/risk`);

    const list = await screen.findByRole("list", { name: "Qualifications" });
    const [item] = within(list).getAllByRole("listitem");
    expect(item).toHaveTextContent("exclusion");
    expect(item).toHaveTextContent("from scope check: Builder's works (sprinkler)");
    const wording = within(item!).getByRole("textbox");
    await userEvent.clear(wording);
    await userEvent.type(wording, "Builder's works are excluded.");
    await userEvent.click(within(item!).getByRole("button", { name: "Save the wording" }));
    await userEvent.click(within(item!).getByRole("button", { name: "History" }));

    const patch = calls.find((call) => call.method === "PATCH");
    expect(JSON.parse(patch!.body!)).toEqual({ text: "Builder's works are excluded." });
    expect(await within(item!).findByText(/qualification: edited · as agreed/)).toBeVisible();
  });

  // req: FR-RSK-06
  it("records a risk's treatment and owner", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/risk`);

    const list = await screen.findByRole("list", { name: "Risk register" });
    const card = within(list).getByText("Night work").closest("li")!;
    expect(card).toHaveAttribute("data-treated", "no");
    expect(card).toHaveTextContent("no treatment");
    await userEvent.selectOptions(within(card).getByLabelText("Treatment of Night work"), "accept");
    await userEvent.type(within(card).getByLabelText("Owner of Night work"), "Carl");
    await userEvent.click(
      within(card).getByRole("button", { name: "Save the treatment of Night work" }),
    );

    const put = calls.find((call) => call.method === "PUT");
    expect(put!.url).toContain(`/risk/risks/${NIGHT}`);
    expect(JSON.parse(put!.body!)).toEqual({ treatment: "accept", owner: "Carl", status: null });
  });
});

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The BOQ page. What matters: each of our lines shows what it was measured from, or that
 * nothing was and why; a client line's mapping is a proposal until a person confirms it;
 * the variances worth a clarification stand out.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const MAPPING = "55555555-0000-4000-8000-000000000001";

function line(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-0000-4000-8000-000000000001",
    line_key: "k-branch",
    section: "PIPEWORK",
    group_heading: "Pipe",
    item_no: "B1",
    description: "50 mm diameter black steel pipe, screwed",
    level: "L05",
    unit: "m",
    quantity: "72.000",
    allowance_percent: "5.000",
    unit_rate: null,
    amount: null,
    is_provisional: false,
    is_lump_sum: false,
    marker_note: null,
    qto_items: ["QTO-0007"],
    traced: true,
    ...overrides,
  };
}

const BOQ = {
  id: "22222222-0000-4000-8000-000000000001",
  version: 1,
  template_key: "company_standard",
  template_version: 1,
  created_at: "2026-09-28T10:00:00Z",
  lines: [
    line(),
    line({
      id: "11111111-0000-4000-8000-000000000002",
      line_key: "k-flushing",
      item_no: "B2",
      description: "Flushing connection",
      unit: "nr",
      quantity: "1.000",
      allowance_percent: null,
      qto_items: [],
      traced: false,
    }),
  ],
};

const MAPPINGS = [
  {
    client_line_id: "33333333-0000-4000-8000-000000000001",
    client_ref: "Bill 1!12",
    client_item: "B3",
    section: "PIPEWORK",
    description: "50 mm diameter pipe, screwed joints",
    unit: "m",
    quantity: "70",
    kind: "line",
    mapping_id: MAPPING,
    state: "proposed",
    method: "rule",
    maps_to: "k-branch",
    confidence: 0.9,
    reason: "same size and unit",
    variance_percent: 2.9,
    flagged: false,
  },
];

const RECONCILIATION = [
  {
    kind: "mapped",
    client_ref: "Bill 1!12",
    client_item: "B3",
    client_description: "50 mm diameter pipe, screwed joints",
    client_unit: "m",
    client_quantity: "70",
    line_item: "B1",
    line_description: "50 mm diameter black steel pipe, screwed",
    unit: "m",
    measured_quantity: "72.000",
    variance: "2.000",
    variance_percent: "2.9",
    flagged: false,
    state: "proposed",
    qto_items: ["QTO-0007"],
    evidence_links: [],
  },
  {
    kind: "client_only",
    client_ref: "Bill 1!26",
    client_item: "C3",
    client_description: "Flow switch, 150 mm",
    client_unit: "nr",
    client_quantity: "1",
    line_item: null,
    line_description: null,
    unit: null,
    measured_quantity: null,
    variance: null,
    variance_percent: null,
    flagged: true,
    state: null,
    qto_items: [],
    evidence_links: [],
  },
];

function stubs() {
  return stubApi({
    "/boq/client": () =>
      Response.json([
        {
          id: "44444444-0000-4000-8000-000000000001",
          document_id: "99999999-0000-4000-8000-000000000001",
          filename: "Bill of Quantities.xlsx",
          sheet_name: "Bill 1",
          status: "read",
          header_row: 7,
          column_map: {},
          reason: null,
          proposal: null,
          lines: 16,
        },
      ]),
    "/boq/mappings": () => Response.json(MAPPINGS),
    [`/boq/mappings/${MAPPING}`]: () => Response.json({ ...MAPPINGS[0], state: "confirmed" }),
    "/boq/reconciliation": () => Response.json(RECONCILIATION),
    "/boq/conventions": () =>
      Response.json({
        version: null,
        conventions: [
          {
            name: "fittings",
            label: "Fittings",
            chosen: "enumerated",
            options: [
              { key: "enumerated", text: "Fittings are enumerated separately." },
              { key: "deemed_included", text: "Fittings are deemed included." },
            ],
          },
        ],
        qualification_text: "Measurement conventions\n1. Fittings are enumerated separately.",
      }),
    "/boq/g2": () =>
      Response.json({
        clear: false,
        g1_approved: true,
        boq_built: true,
        untraced_lines: [{ id: BOQ.lines[1]!.id }],
      }),
    "/boq": () => Response.json(BOQ),
  });
}

describe("the BOQ page", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-BOQ-05
  it("shows each line's QTO items, and a line with none holds up G2", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const ours = await screen.findByRole("table", { name: "Our BOQ" });
    const branch = within(ours).getByText(/50 mm diameter black steel pipe/).closest("tr")!;
    expect(branch).toHaveTextContent("QTO-0007");
    const flushing = within(ours).getByText(/Flushing connection/).closest("tr")!;
    expect(flushing).toHaveTextContent("no QTO trace");
    expect(within(flushing).getByRole("button", { name: "Provisional" })).toBeVisible();
    expect(screen.getByRole("status", { name: "G2" })).toHaveTextContent(
      "1 line(s) with no QTO trace",
    );
  });

  // req: FR-BOQ-02
  it("confirms a proposed mapping", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Mappings" });
    const row = within(table).getByText("50 mm diameter pipe, screwed joints").closest("tr")!;
    expect(row).toHaveTextContent("+2.9%");
    await userEvent.click(within(row).getByRole("button", { name: "Confirm" }));

    const posted = calls.find((c) => c.method === "POST" && c.url.includes(MAPPING));
    expect(JSON.parse(posted!.body!)).toMatchObject({ decision: "confirm" });
  });

  // req: FR-BOQ-03
  it("marks what to clarify in the reconciliation", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Reconciliation" });
    const flow = within(table).getByText(/Flow switch/).closest("tr")!;
    expect(flow).toHaveAttribute("data-flagged", "true");
    expect(flow).toHaveTextContent("not measured");
    expect(screen.getByRole("heading", { name: /Reconciliation/ })).toHaveTextContent(
      "1 to clarify",
    );
  });
});

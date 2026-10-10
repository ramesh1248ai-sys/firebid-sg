import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The rate library page and the BOQ's pricing. What matters: every rate shows its source and
 * validity; a bad list imports nothing and says what to fix; an unpriced line says so and is
 * left out of the total; a proposed rate waits for a person.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const LINE = "11111111-0000-4000-8000-000000000001";

function rate(overrides: Record<string, unknown> = {}) {
  return {
    id: "77777777-0000-4000-8000-000000000001",
    item_key: "pipe|50||||",
    label: "pipe, DN50",
    key_parts: { type: "pipe", dn: "50", material: "", schedule: "", joining: "", brand: "" },
    description: "Black steel pipe DN50",
    unit: "m",
    unit_rate: "21.35",
    source_type: "company_standard",
    source_reference: "CS-2026",
    effective_from: "2026-01-01",
    valid_until: "2027-12-31",
    version: 1,
    retired_at: null,
    ...overrides,
  };
}

function line(overrides: Record<string, unknown> = {}) {
  return {
    line_id: LINE,
    item_no: "A7",
    section: "FIRE SPRINKLER INSTALLATION",
    description: "50 mm pipe (branch)",
    unit: "m",
    quantity: "72.000",
    item_key: "pipe|50||||",
    status: "priced",
    unit_rate: "21.35",
    amount: "1537.20",
    method: "rule",
    reason: "the library entry for pipe, DN50",
    rate: rate(),
    proposed: null,
    superseded: false,
    warnings: [],
    awaiting_model: false,
    ...overrides,
  };
}

describe("the rate library", () => {
  // req: FR-CST-01
  it("lists each rate with its source and validity, and reports a bad import row by row", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const calls = stubApi({
      "/rates/import": () =>
        Response.json({
          imported: false,
          sheet: "Rates",
          created: 0,
          superseded: 0,
          unchanged: 0,
          problems: [{ row: 14, column: "rate", message: "-3 is not a positive amount" }],
        }),
      "/rates": () => Response.json([rate()]),
    });
    renderAt("/rates");

    const table = await screen.findByRole("table", { name: "Rates" });
    const row = within(table).getByText("Black steel pipe DN50").closest("tr")!;
    expect(row).toHaveTextContent("21.35");
    expect(row).toHaveTextContent("Company standard: CS-2026");
    expect(row).toHaveTextContent("2027-12-31");

    const file = new File(["PK"], "rates.xlsx");
    await userEvent.upload(screen.getByLabelText(/Import a rate list/), file);
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    const problems = await screen.findByRole("table", { name: "Import problems" });
    expect(problems).toHaveTextContent("row 14");
    expect(problems).toHaveTextContent("-3 is not a positive amount");
    expect(screen.getByText(/Nothing was imported/)).toBeVisible();
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/rates/import"))).toBe(true);
  });

  // req: FR-CST-01
  it("does not offer an estimator the import", async () => {
    signedInAs({ name: "Ethan Lim", preferred_username: "estimator@firebid.test", roles: ["estimator"] });
    stubApi({ "/rates": () => Response.json([rate()]) });
    renderAt("/rates");

    await screen.findByRole("table", { name: "Rates" });
    expect(screen.queryByLabelText(/Import a rate list/)).toBeNull();
  });
});

describe("the productivity library", () => {
  const entry = {
    id: "88888888-0000-4000-8000-000000000001",
    item_type: "pipe",
    dn: "50",
    joining: "",
    description: "Pipe DN50, screwed",
    unit: "m",
    hours_per_unit: "0.30",
    trade: "pipefitter",
    source_type: "company_standard",
    source_reference: "PS-2026",
    source: "company standard: PS-2026",
    version: 1,
    retired_at: null,
    created_at: "2026-10-01T00:00:00Z",
  };

  // req: FR-LAB-01
  it("lists each entry with its source, and reports a bad list row by row", async () => {
    signedInAs({
      name: "Sam Lim",
      preferred_username: "senior.estimator@firebid.test",
      roles: ["senior_estimator"],
    });
    const calls = stubApi({
      "/labour/productivity/import": () =>
        Response.json({
          imported: false,
          sheet: "Productivity",
          created: 0,
          superseded: 0,
          unchanged: 0,
          problems: [
            { row: 9, column: "man-hours per unit", message: "0 is not a positive figure" },
          ],
        }),
      "/labour/productivity": () => Response.json([entry]),
      "/rates": () => Response.json([rate()]),
    });
    renderAt("/rates");

    const table = await screen.findByRole("table", { name: "Productivity" });
    const row = within(table).getByText("Pipe DN50, screwed").closest("tr")!;
    expect(row).toHaveTextContent("pipe, DN50");
    expect(row).toHaveTextContent("0.30");
    expect(row).toHaveTextContent("company standard: PS-2026");

    const file = new File(["PK"], "productivity.xlsx");
    await userEvent.upload(screen.getByLabelText(/Import a productivity list/), file);
    await userEvent.click(screen.getByRole("button", { name: "Import the productivity list" }));

    const problems = await screen.findByRole("table", { name: "Productivity import problems" });
    expect(problems).toHaveTextContent("row 9");
    expect(problems).toHaveTextContent("0 is not a positive figure");
    expect(
      calls.some((c) => c.method === "POST" && c.url.includes("/labour/productivity/import")),
    ).toBe(true);
  });

  // req: FR-LAB-01
  it("says an empty library gives no line hours, and offers an estimator no import", async () => {
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    });
    stubApi({
      "/labour/productivity": () => Response.json([]),
      "/rates": () => Response.json([rate()]),
    });
    renderAt("/rates");

    expect(
      await screen.findByText(/no BOQ line has labour hours until a list is imported/),
    ).toBeVisible();
    expect(screen.queryByLabelText(/Import a productivity list/)).toBeNull();
  });
});

describe("pricing the BOQ", () => {
  function stubs(lines: ReturnType<typeof line>[]) {
    return stubApi({
      [`/pricing/lines/${LINE}/confirm`]: () =>
        Response.json({
          tender_validity_end: "2027-01-13",
          lines: lines.map((l) => ({ ...l, status: "priced", unit_rate: "41.80", amount: "125.40" })),
          totals: { sections: {}, priced: "125.40", allowances: "0.00", grand: "125.40", unpriced: 0, gst_included: false },
        }),
      "/pricing": () =>
        Response.json({
          tender_validity_end: "2027-01-13",
          lines,
          totals: {
            sections: { "FIRE SPRINKLER INSTALLATION": "1537.20" },
            priced: "1537.20",
            allowances: "0.00",
            grand: "1537.20",
            unpriced: 1,
            gst_included: false,
          },
        }),
      "/boq/client": () => Response.json([]),
      "/boq/mappings": () => Response.json([]),
      "/boq/reconciliation": () => Response.json([]),
      "/boq/conventions": () =>
        Response.json({ version: null, conventions: [], qualification_text: "" }),
      "/boq/g2": () => Response.json({ clear: false, g1_approved: true, boq_built: true, untraced_lines: [] }),
      "/boq": () =>
        Response.json({
          id: "22222222-0000-4000-8000-000000000001",
          version: 1,
          template_key: "company_standard",
          template_version: 1,
          created_at: "2026-09-28T10:00:00Z",
          lines: [],
        }),
    });
  }

  // req: FR-CST-01
  it("says which lines are unpriced, warns of short validity, and leaves unpriced lines out of the total", async () => {
    signedInAs({ name: "Ethan Lim", preferred_username: "estimator@firebid.test", roles: ["estimator"] });
    stubs([
      line({
        warnings: [
          {
            code: "ends_before_tender_validity",
            message: "the rate is valid until 31 Oct 2026, before the tender validity ends on 13 Jan 2027",
          },
        ],
      }),
      line({
        line_id: "11111111-0000-4000-8000-000000000002",
        item_no: "A12",
        description: "150 mm check valve",
        unit: "no",
        quantity: "1",
        status: "unpriced",
        unit_rate: null,
        amount: null,
        method: null,
        rate: null,
      }),
    ]);
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Pricing" });
    const valve = within(table).getByText("150 mm check valve").closest("tr")!;
    expect(valve).toHaveAttribute("data-status", "unpriced");
    expect(valve).toHaveTextContent("unpriced");
    expect(within(table).getByText(/before the tender validity ends/)).toBeVisible();
    expect(screen.getByLabelText("Grand total")).toHaveTextContent("1537.20");
    expect(table).toHaveTextContent("1 unpriced line(s) not included");
  });

  // req: FR-CST-01
  it("prices a proposed line only when a person confirms it", async () => {
    signedInAs({ name: "Ethan Lim", preferred_username: "estimator@firebid.test", roles: ["estimator"] });
    const calls = stubs([
      line({
        description: "Tee, DN100xDN50",
        unit: "no",
        quantity: "3",
        status: "proposed",
        unit_rate: null,
        amount: null,
        method: null,
        rate: null,
        reason: "the entry names a brand the line leaves open",
        proposed: rate({ description: "Grooved tee 100x50", unit_rate: "41.80", source_type: "quotation", source_reference: "Q-2026-1003" }),
      }),
    ]);
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Pricing" });
    const tee = within(table).getByText("Tee, DN100xDN50").closest("tr")!;
    expect(tee).toHaveTextContent("proposed: confirm");
    expect(tee).toHaveTextContent("(41.80)");
    await userEvent.click(within(tee).getByRole("button", { name: "Confirm" }));

    expect(calls.some((c) => c.method === "POST" && c.url.includes(`/pricing/lines/${LINE}/confirm`))).toBe(true);
  });
});

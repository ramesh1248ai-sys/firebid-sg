import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * Supplier quotations and the cost build-up on the BOQ page. What matters: a quotation's
 * fields are shown beside the line of the file each was read from, and what the file does
 * not state stands out; each line is linked to what it prices before it is confirmed; the
 * build-up shows every component with its basis and whose figure it is, and GST apart.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const QUOTE = "88888888-0000-4000-8000-000000000001";
const QUOTE_LINE = "88888888-0000-4000-8000-000000000011";
const BOQ_LINE = "11111111-0000-4000-8000-000000000001";

const BOQ = {
  id: "22222222-0000-4000-8000-000000000001",
  version: 1,
  template_key: "company_standard",
  template_version: 1,
  created_at: "2026-09-28T10:00:00Z",
  lines: [
    {
      id: BOQ_LINE,
      line_key: "k-check",
      section: "VALVES",
      group_heading: "Valves",
      item_no: "C4",
      description: "Check valve, DN150",
      level: "L01",
      unit: "no",
      quantity: "2.000",
      allowance_percent: null,
      unit_rate: null,
      amount: null,
      is_provisional: false,
      is_lump_sum: false,
      marker_note: null,
      qto_items: ["QTO-0031"],
      traced: true,
    },
  ],
};

function quotation(overrides: Record<string, unknown> = {}) {
  return {
    id: QUOTE,
    filename: "PV-Q-2026-1042.pdf",
    kind: "pdf",
    state: "extracted",
    supplier: "Pacific Valve Co. Ltd",
    quote_number: "PV-Q-2026-1042",
    quote_date: "2026-09-15",
    valid_until: "2026-12-14",
    currency: "USD",
    delivery_terms: "FOB Shanghai",
    incoterm: "FOB",
    lead_time: null,
    exclusions: ["Import duties and GST"],
    fields: {
      supplier: {
        value: "Pacific Valve Co. Ltd",
        source: { part: "document", page: 1, row: 2, text: "Supplier: | Pacific Valve Co. Ltd" },
      },
      quote_number: {
        value: "PV-Q-2026-1042",
        source: { part: "document", page: 1, row: 3, text: "Quotation No: | PV-Q-2026-1042" },
      },
    },
    missing: [],
    method: "rules",
    model: null,
    flags: [
      {
        code: "ends_before_tender",
        message:
          "the quotation is valid until 2026-12-14, before the tender's validity ends on 2027-01-13",
      },
      { code: "exclusions", message: "the quotation excludes: Import duties and GST" },
    ],
    decided_by: null,
    decided_at: null,
    note: null,
    lines: [
      {
        id: QUOTE_LINE,
        ordinal: 1,
        description: "Check valve flanged DN150",
        brand: "Pacific",
        model: "CV-150F",
        unit: "no",
        unit_price: "86.5000",
        moq: "10",
        lead_time: "8 weeks",
        source: {
          part: "document",
          page: 1,
          row: 10,
          text: "Check valve flanged DN150 | Pacific | CV-150F | no | 86.50 | 10 | 8 weeks",
        },
        item_key: null,
        item_label: null,
        boq_line_id: null,
        rate_id: null,
        landed: null,
      },
    ],
    source_lines: [
      { part: "document", page: 1, row: 2, cells: ["Supplier:", "Pacific Valve Co. Ltd"] },
      { part: "document", page: 1, row: 3, cells: ["Quotation No:", "PV-Q-2026-1042"] },
      { part: "document", page: 1, row: 4, cells: ["Thank you for your enquiry."] },
    ],
    ...overrides,
  };
}

function component(key: string, label: string, overrides: Record<string, unknown> = {}) {
  return {
    component: key,
    label,
    basis: "not set",
    detail: "nobody has entered it",
    source: "not set",
    amount: null,
    entered: true,
    bases: [],
    ...overrides,
  };
}

const BUILD_UP = {
  lines: [
    component("materials", "Materials", {
      basis: "calculated",
      detail: "12 priced bill line(s)",
      source: "the priced bill of quantities",
      amount: "8400.00",
      entered: false,
    }),
    component("labour", "Labour", {
      basis: "lump_sum",
      detail: "a lump sum",
      source: "entered by Ethan Lim on 2026-10-03",
      amount: "1600.00",
    }),
    component("margin", "Margin", { bases: ["direct", "cost", "cost_with_contingency"] }),
  ],
  direct: "10000.00",
  cost: "10000.00",
  cost_with_contingency: "10000.00",
  total: "10000.00",
  gst_percent: "9",
  gst_effective_from: "2024-01-01",
  gst: "900.00",
  total_with_gst: "10900.00",
  priced_on: "2026-10-03",
  priced_on_set: false,
  unpriced_lines: 2,
  not_set: ["margin"],
  base_labels: { direct: "direct cost", cost: "cost", cost_with_contingency: "cost with contingency" },
};

const OUTLIER = {
  line_id: BOQ_LINE,
  item_no: "C4",
  description: "Check valve, DN150",
  item_key: "check_valve|150||||",
  unit: "no",
  price: "126.85",
  history: 3,
  low: "88.0000",
  high: "92.0000",
  median: "90.0000",
  latest: { unit_price: "92", on: "2026-02-01", kind: "project", reference: "Punggol Mall" },
  deviation_percent: "40.9",
  tolerance_percent: "15",
  outlier: true,
};

function stubs(extra: Record<string, (call: Call) => Response> = {}) {
  return stubApi({
    "/boq/client": () => Response.json([]),
    "/boq/mappings": () => Response.json([]),
    "/boq/reconciliation": () => Response.json([]),
    "/boq/conventions": () =>
      Response.json({ version: null, conventions: [], qualification_text: "" }),
    "/pricing": () => new Response("no prices", { status: 404 }),
    "/boq/g2": () =>
      Response.json({
        clear: false,
        g1_approved: true,
        boq_built: true,
        untraced_lines: [],
        unsourced_lines: [{ id: BOQ_LINE }],
      }),
    "/boq": () => Response.json(BOQ),
    "/quotations": () => Response.json([quotation()]),
    [`/quotations/${QUOTE}`]: () => Response.json(quotation()),
    "/cost/build-up": () => Response.json(BUILD_UP),
    "/cost/history": () => Response.json([OUTLIER]),
    ...extra,
  });
}

describe("quotations and the cost build-up", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-CST-03
  it("flags a quotation that ends before the tender does, and one with exclusions", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Quotations" });
    const row = within(table).getByText("Pacific Valve Co. Ltd").closest("tr")!;
    expect(row).toHaveTextContent("before the tender's validity ends on 2027-01-13");
    expect(row).toHaveTextContent("the quotation excludes: Import duties and GST");
    expect(row).toHaveTextContent("to confirm");
  });

  // req: FR-CST-02
  it("shows each field beside the line of the file it was read from", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    await userEvent.click(await screen.findByRole("button", { name: "Check" }));

    const fields = await screen.findByRole("table", { name: "Quotation fields" });
    const number = within(fields).getByText("Quotation number").closest("tr")!;
    expect(within(number).getByRole("textbox")).toHaveValue("PV-Q-2026-1042");
    expect(number).toHaveTextContent("Quotation No: | PV-Q-2026-1042");
    // Nothing in the file states a lead time, and the view says so.
    const lead = within(fields).getByText("Lead time").closest("tr")!;
    expect(lead).toHaveTextContent("not stated in the file");
    // The file itself, with the lines that were read from marked.
    const file = screen.getByRole("list", { name: "Source file" });
    const marked = [...file.querySelectorAll("mark")].map((mark) => mark.textContent);
    expect(marked).toEqual(["Supplier: | Pacific Valve Co. Ltd", "Quotation No: | PV-Q-2026-1042"]);
  });

  // req: FR-CST-02
  it("links a quotation line to the BOQ line it prices, and confirms", async () => {
    const linked = quotation({
      lines: [{ ...quotation().lines[0], boq_line_id: BOQ_LINE, item_key: "check_valve|150||||" }],
    });
    const calls = stubs({
      [`/lines/${QUOTE_LINE}/link`]: () => Response.json(linked),
      [`/quotations/${QUOTE}/confirm`]: () =>
        Response.json({ ...linked, state: "confirmed", decided_by: "Sam Senior" }),
    });
    renderAt(`/bids/${BID}/boq`);
    await userEvent.click(await screen.findByRole("button", { name: "Check" }));

    const lines = await screen.findByRole("table", { name: "Quotation lines" });
    expect(lines).toHaveTextContent("86.50 USD");
    await userEvent.selectOptions(
      within(lines).getByRole("combobox", { name: "BOQ line for Check valve flanged DN150" }),
      BOQ_LINE,
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm the quotation" }));

    const link = calls.find((call) => call.url.includes("/link"));
    expect(JSON.parse(link!.body!)).toEqual({ boq_line_id: BOQ_LINE, item_key: null });
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/confirm"))).toBe(
      true,
    );
  });

  // req: FR-CST-05, FR-CST-06
  it("shows every component with its basis and source, and GST apart from the total", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Cost build-up" });
    const materials = within(table).getByText("Materials").closest("tr")!;
    expect(materials).toHaveTextContent("calculated");
    expect(materials).toHaveTextContent("the priced bill of quantities");
    const labour = within(table).getByText("Labour").closest("tr")!;
    expect(labour).toHaveTextContent("lump sum");
    expect(labour).toHaveTextContent("entered by Ethan Lim on 2026-10-03");
    expect(within(table).getByText("Margin").closest("tr")).toHaveAttribute(
      "data-basis",
      "not set",
    );
    // Prices are held exclusive of GST, which is shown apart.
    expect(within(table).getByLabelText("Total excluding GST")).toHaveTextContent("10000.00");
    expect(within(table).getByLabelText("GST")).toHaveTextContent("900.00");
    expect(table).toHaveTextContent("GST at 9% (the rate from 2024-01-01)");
    expect(within(table).getByLabelText("Total including GST")).toHaveTextContent("10900.00");
  });

  // req: FR-CST-06
  it("records an estimator's figure for a component", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Cost build-up" });
    const margin = within(table).getByText("Margin").closest("tr")!;
    await userEvent.click(within(margin).getByRole("button", { name: "Enter…" }));
    await userEvent.selectOptions(within(margin).getByLabelText("Base for Margin"), "cost");
    await userEvent.type(within(margin).getByLabelText("Figure for Margin"), "8");
    await userEvent.click(within(margin).getByRole("button", { name: "Save" }));

    const put = calls.find((call) => call.method === "PUT");
    expect(put!.url).toContain("/cost/build-up/margin");
    expect(JSON.parse(put!.body!)).toEqual({ basis: "percentage", percent: "8", base: "cost" });
  });

  // req: FR-CST-07
  it("shows a price unlike its history with the comparison", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Prices unlike their history" });
    expect(table).toHaveTextContent("C4 Check valve, DN150");
    expect(table).toHaveTextContent("+40.9% against a median of 90.00 over 3 past price(s)");
  });

  // req: FR-CST-09
  it("says at G2 that a priced line has no source", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    expect(await screen.findByRole("status", { name: "G2" })).toHaveTextContent(
      "1 priced line(s) with no source",
    );
  });
});

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * The labour estimate on the BOQ page. What matters: a line shows its baseline hours and
 * each multiplier separately, each with where it comes from; a proposed condition waits
 * for a person; the rate build-up shows its table's effective date.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const HEADS = "11111111-0000-4000-8000-000000000001";
const VALVE = "11111111-0000-4000-8000-000000000002";

const BOQ = {
  id: "22222222-0000-4000-8000-000000000001",
  version: 1,
  template_key: "company_standard",
  template_version: 1,
  created_at: "2026-09-28T10:00:00Z",
  lines: [],
};

function component(key: string, label: string, hourly: string) {
  return { key, label, hourly, basis: `${label} / 208 h` };
}

const TRADE = {
  trade: "sprinkler_fitter",
  label: "Sprinkler fitter",
  crew: [
    { grade: "skilled", percent: "60" },
    { grade: "general", percent: "40" },
  ],
  grades: [
    {
      grade: "skilled",
      label: "Skilled worker",
      components: [
        component("wage", "Wages", "12.5000"),
        component("levy", "Foreign worker levy", "2.4038"),
        component("supervision", "Supervision", "2.8209"),
      ],
      hourly: "21.3401",
    },
    {
      grade: "general",
      label: "General worker",
      components: [
        component("wage", "Wages", "8.6538"),
        component("levy", "Foreign worker levy", "3.3654"),
        component("supervision", "Supervision", "2.8209"),
      ],
      hourly: "18.1863",
    },
  ],
  hourly: "20.0786",
  effective_from: "2025-01-01",
  source: "Company labour rate table 2025",
};

const HEIGHT = {
  key: "height_4_5m_to_6m",
  label: "Installation height 4.5 m to 6.0 m",
  kind: "height",
  value: "1.25",
  source: "Company standard",
  rationale: "Scissor lift or tower scaffold.",
  up_to_mm: 6000,
};
const NIGHT = {
  key: "night_work",
  label: "Night work",
  kind: "condition",
  value: "1.20",
  source: "Company standard",
  rationale: "Lower output at night.",
  up_to_mm: null,
};

function labour(overrides: Record<string, unknown> = {}) {
  return {
    priced_on: "2026-10-03",
    lines: [
      {
        line_id: HEADS,
        reference: "A1",
        description: "Pendent sprinkler head",
        section: "FIRE SPRINKLER INSTALLATION",
        level: "L05",
        unit: "nr",
        quantity: "18.000",
        hours_per_unit: "0.4000",
        productivity_source: "company standard: PS-2026",
        productivity_entry_id: "33333333-0000-4000-8000-000000000001",
        trade: "sprinkler_fitter",
        baseline_hours: "7.20",
        multipliers: [
          {
            key: HEIGHT.key,
            label: HEIGHT.label,
            value: "1.25",
            source: HEIGHT.source,
            rationale: HEIGHT.rationale,
            scope: "L05",
            confirmed_by: "Ethan Lim",
            basis: "ceiling height 5200 mm (entered by Ethan Lim)",
          },
          {
            key: NIGHT.key,
            label: NIGHT.label,
            value: "1.20",
            source: NIGHT.source,
            rationale: NIGHT.rationale,
            scope: "the whole bid",
            confirmed_by: "Sam Senior",
            basis: "tender clause 1.4",
          },
        ],
        factor: "1.5000",
        hours: "10.80",
        hourly_rate: "20.0786",
        cost: "216.85",
        reason: "",
      },
      {
        line_id: VALVE,
        reference: "C4",
        description: "150 mm check valve",
        section: "FIRE SPRINKLER INSTALLATION",
        level: null,
        unit: "nr",
        quantity: "2.000",
        hours_per_unit: null,
        productivity_source: null,
        productivity_entry_id: null,
        trade: null,
        baseline_hours: null,
        multipliers: [],
        factor: "1",
        hours: null,
        hourly_rate: null,
        cost: null,
        reason: "no productivity entry",
      },
    ],
    by_section: [
      {
        key: "FIRE SPRINKLER INSTALLATION",
        label: "FIRE SPRINKLER INSTALLATION",
        baseline_hours: "7.20",
        hours: "10.80",
        cost: "216.85",
        hourly_rate: null,
      },
    ],
    by_trade: [
      {
        key: "sprinkler_fitter",
        label: "Sprinkler fitter",
        baseline_hours: "7.20",
        hours: "10.80",
        cost: "216.85",
        hourly_rate: "20.0786",
      },
    ],
    baseline_hours: "7.20",
    hours: "10.80",
    cost: "216.85",
    without_hours: 1,
    conditions: [
      {
        id: "44444444-0000-4000-8000-000000000001",
        level: null,
        multiplier_key: "high_rise",
        label: "High-rise logistics",
        value: "1.10",
        source: "Company standard",
        rationale: "Hoist waiting time and vertical travel.",
        state: "proposed",
        basis: "24 levels served (entered by Ethan Lim); high-rise from 20",
        proposed_by: "platform",
        decided_by: null,
        decided_at: null,
      },
    ],
    levels: ["L05"],
    catalogue: {
      on: "2026-10-03",
      multipliers: [HEIGHT, NIGHT],
      rate_table_effective_from: "2025-01-01",
      rate_table_source: "Company labour rate table 2025",
      rate_tables: ["2025-01-01"],
      trades: [TRADE],
    },
    ...overrides,
  };
}

function stubs(extra: Record<string, (call: Call) => Response> = {}) {
  return stubApi({
    "/boq/client": () => Response.json([]),
    "/boq/mappings": () => Response.json([]),
    "/boq/reconciliation": () => Response.json([]),
    "/boq/conventions": () =>
      Response.json({ version: null, conventions: [], qualification_text: "" }),
    "/boq/g2": () =>
      Response.json({ clear: true, g1_approved: true, boq_built: true, untraced_lines: [] }),
    "/boq": () => Response.json(BOQ),
    "/labour": () => Response.json(labour()),
    ...extra,
  });
}

describe("the labour estimate", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-LAB-01, FR-LAB-02
  it("shows baseline hours and each multiplier separately, each with its source", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Labour" });
    const heads = within(table).getByText("Pendent sprinkler head").closest("tr")!;
    expect(heads).toHaveTextContent("7.20 h");
    expect(heads).toHaveTextContent("0.4 h per nr · company standard: PS-2026");
    expect(heads).toHaveTextContent("× 1.25 Installation height 4.5 m to 6.0 m");
    expect(heads).toHaveTextContent("L05 · confirmed by Ethan Lim · Company standard");
    expect(heads).toHaveTextContent("× 1.20 Night work");
    expect(heads).toHaveTextContent("the whole bid · confirmed by Sam Senior");
    expect(heads).toHaveTextContent("10.80");
    expect(heads).toHaveTextContent("216.85");
    // A line the library has no entry for says so, and has no hours.
    const valve = within(table).getByText("150 mm check valve").closest("tr")!;
    expect(valve).toHaveAttribute("data-hours", "none");
    expect(valve).toHaveTextContent("no productivity entry");
    expect(screen.getByRole("table", { name: "Labour by trade" })).toHaveTextContent(
      "Sprinkler fitter",
    );
    expect(screen.getByRole("table", { name: "Labour by system" })).toHaveTextContent(
      "FIRE SPRINKLER INSTALLATION",
    );
  });

  // req: FR-LAB-02
  it("leaves a proposed condition to a person, with its source and rationale", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/boq`);

    const table = await screen.findByRole("table", { name: "Site conditions" });
    const row = within(table).getByText("High-rise logistics").closest("tr")!;
    expect(row).toHaveAttribute("data-state", "proposed");
    expect(row).toHaveTextContent("proposed by the platform");
    expect(row).toHaveTextContent("24 levels served");
    expect(row).toHaveTextContent("Hoist waiting time and vertical travel.");
    expect(row).toHaveTextContent("Company standard");

    await userEvent.click(within(row).getByRole("button", { name: "Confirm" }));

    const put = calls.find((call) => call.method === "PUT");
    expect(put!.url).toContain("/labour/conditions");
    expect(JSON.parse(put!.body!)).toEqual({ key: "high_rise", level: null, state: "confirmed" });
  });

  // req: FR-LAB-02
  it("adds a condition for a level only with a reason", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/boq`);

    await screen.findByRole("table", { name: "Labour" });
    const add = screen.getByRole("button", { name: "Confirm for the bid" });
    await userEvent.selectOptions(screen.getByLabelText("Multiplier"), "night_work");
    await userEvent.selectOptions(screen.getByLabelText("Applies to"), "L05");
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why it applies"), "night shifts on L05");
    await userEvent.click(add);

    const put = calls.find((call) => call.method === "PUT");
    expect(JSON.parse(put!.body!)).toEqual({
      key: "night_work",
      level: "L05",
      state: "confirmed",
      basis: "night shifts on L05",
    });
  });

  // req: FR-LAB-03
  it("shows the rate build-up line by line with its table's effective date", async () => {
    stubs();
    renderAt(`/bids/${BID}/boq`);

    await screen.findByRole("table", { name: "Labour" });
    expect(
      screen.getByText(/the table effective from 2025-01-01 \(Company labour rate table 2025\)/),
    ).toHaveTextContent("for a bid priced on 2026-10-03");
    await userEvent.click(screen.getByText(/Sprinkler fitter: SGD 20.08 an hour/));
    const rate = screen.getByRole("table", { name: "Rate build-up for Sprinkler fitter" });
    const levy = within(rate).getByText("Foreign worker levy").closest("tr")!;
    expect(levy).toHaveTextContent("2.4038");
    expect(levy).toHaveTextContent("3.3654");
    expect(within(rate).getByText("An hour").closest("tr")).toHaveTextContent("21.3401");
  });
});

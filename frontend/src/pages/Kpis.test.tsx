import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/** The KPI dashboard: each bid's own measures, flagged against target, and its AI cost. */

const KPIS = {
  agent_runs: 4,
  escalation_rate: 0.25,
  success_rate: 0.75,
  cost_sgd: "0.85",
  target_cost_sgd: "50.00",
  targets: {},
  bids: [
    {
      bid_id: "6a1f0c3e-0000-4000-8000-000000000001",
      human_id: "BID-2026-048",
      ai_items: 13,
      verified_items: 13,
      false_detection_rate: 0.08,
      missed_item_rate: 0.0,
      duplicate_groups: 2,
      unresolved_duplicates: 0,
      minutes_on_task: 42,
      agent_runs: 4,
      escalation_rate: 0.25,
      success_rate: 0.75,
      cost_sgd: "0.85",
      budget_sgd: null,
      target_cost_sgd: "50.00",
      cost_by_model: [
        { route: "boq_mapping", provider: "openai", model: "gpt-5.1", runs: 3, cost_sgd: "0.75" },
      ],
    },
  ],
};

const PHASE2 = {
  turnaround_working_days: 13,
  baseline_turnaround_working_days: 20,
  turnaround_reduction: 0.35,
  price_provenance: 1,
  clarification_acceptance: 0.5,
  targets: {},
  minor_edit_ratio: 0.2,
  bids: [
    {
      bid_id: "6a1f0c3e-0000-4000-8000-000000000001",
      human_id: "BID-2026-048",
      received_on: "2026-09-14",
      ready_on: "2026-10-01",
      turnaround_working_days: 13,
      priced_lines: 16,
      sourced_lines: 16,
      price_provenance: 1,
      clarifications_issued: 2,
      clarifications_measured: 2,
      clarifications_minor: 1,
      clarification_acceptance: 0.5,
    },
    {
      bid_id: "6a1f0c3e-0000-4000-8000-000000000002",
      human_id: "BID-2026-051",
      received_on: "2026-09-28",
      ready_on: null,
      turnaround_working_days: null,
      priced_lines: 0,
      sourced_lines: 0,
      price_provenance: null,
      clarifications_issued: 0,
      clarifications_measured: 0,
      clarifications_minor: 0,
      clarification_acceptance: null,
    },
  ],
};

describe("the KPI dashboard", () => {
  // req: NFR-14
  it("shows the Phase 2 KPIs against their targets, and says what is not measured yet", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    stubApi({ "/kpis/phase2": () => Response.json(PHASE2), "/kpis": () => Response.json(KPIS) });
    renderAt("/kpis");

    const totals = await screen.findByLabelText("Phase 2 totals");
    expect(totals).toHaveTextContent("13.0 working days");
    expect(totals).toHaveTextContent("target −30% against 20 days: −35%");
    expect(within(totals).getByText("100.0%")).not.toHaveClass("text-amber-700");
    expect(within(totals).getByText("50.0%")).toHaveClass("text-amber-700");

    const table = screen.getByRole("table", { name: "Phase 2 by bid" });
    const ready = within(table).getByText("BID-2026-048").closest("tr")!;
    expect(ready).toHaveTextContent("16 of 16");
    expect(ready).toHaveTextContent("1 of 2");
    const open = within(table).getByText("BID-2026-051").closest("tr")!;
    expect(open).toHaveTextContent("not yet");
  });

  it("says there is no baseline rather than showing a reduction", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    stubApi({
      "/kpis/phase2": () =>
        Response.json({ ...PHASE2, baseline_turnaround_working_days: null, turnaround_reduction: null }),
      "/kpis": () => Response.json(KPIS),
    });
    renderAt("/kpis");

    expect(await screen.findByLabelText("Phase 2 totals")).toHaveTextContent("no baseline recorded yet");
  });

  // req: NFR-15
  it("flags what misses its target and breaks a bid's AI cost down by route and model", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    stubApi({ "/kpis/phase2": () => Response.json(PHASE2), "/kpis": () => Response.json(KPIS) });
    renderAt("/kpis");

    const totals = await screen.findByLabelText("Totals");
    expect(totals).toHaveTextContent("75.0%");
    expect(within(totals).getByText("75.0%")).toHaveClass("text-amber-700");
    const table = screen.getByRole("table", { name: "Bids" });
    const row = within(table).getByText("BID-2026-048").closest("tr")!;
    expect(within(row).getByText("8.0%")).toHaveClass("text-amber-700");
    expect(row).toHaveTextContent("42 min");

    await userEvent.click(within(row).getByRole("button", { name: "SGD 0.85" }));

    expect(within(table).getByText(/boq_mapping · openai · gpt-5.1/)).toBeVisible();
  });
});

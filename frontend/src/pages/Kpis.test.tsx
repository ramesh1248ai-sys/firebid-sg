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

describe("the KPI dashboard", () => {
  // req: NFR-15
  it("flags what misses its target and breaks a bid's AI cost down by route and model", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    stubApi({ "/kpis": () => Response.json(KPIS) });
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

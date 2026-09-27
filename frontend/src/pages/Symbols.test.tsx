import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The symbols page and the library page. What matters: nothing unmapped hides. Every symbol
 * nobody has confirmed is listed with how often it appears, and each legend row carries the
 * proposal, who or what made it, and the decision.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const LINEAGE = "44444444-0000-4000-8000-000000000001";

function mapping(overrides: Record<string, unknown> = {}) {
  return {
    lineage_id: LINEAGE,
    version: 1,
    state: "proposed",
    source: "model",
    object_type: "sprinkler_upright",
    attributes: {},
    consultant: "ALPHA CONSULTANTS PTE LTD",
    project_only: false,
    description: "SPRINKLER - UP TYPE",
    confidence: 0.82,
    model: "claude-opus-5",
    prompt_version: "b0f6f621ff104d50",
    rule_version: null,
    reason: "a circle with a dot is an upright head",
    confirmed_by: null,
    change_note: null,
    created_at: "2026-09-28T01:00:00+00:00",
    ...overrides,
  };
}

const LEGEND = [
  {
    id: "55555555-0000-4000-8000-000000000001",
    sheet_id: "22222222-0000-4000-8000-000000000001",
    heading: "LEGEND",
    description: "SPRINKLER - UP TYPE",
    status: "proposed",
    symbol_box: [20, 65, 25, 70],
    has_crop: false,
    mapping: mapping(),
  },
];

const COUNTS = {
  counted: [
    {
      object_type: "sprinkler_pendent",
      label: "Sprinkler, pendent",
      count: 4,
      sheets: 1,
    },
  ],
  unmapped: [
    {
      symbol_key: "block:53b59f",
      instances: 3,
      status: "no legend",
      description: null,
      block: "UNK-01",
      mapping_lineage_id: null,
      proposed_type: null,
      sheet_ids: ["22222222-0000-4000-8000-000000000001"],
    },
  ],
  not_objects: 0,
};

const CHOICES = [
  {
    key: "sprinkler_upright",
    label: "Sprinkler, upright",
    category: "sprinkler",
    attribute_schema: {},
  },
  {
    key: "sprinkler_pendent",
    label: "Sprinkler, pendent",
    category: "sprinkler",
    attribute_schema: {},
  },
];

describe("the symbols page", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-VIS-02
  it("lists every unmapped symbol with how often it appears, apart from the counts", async () => {
    stubApi({
      "/symbols/legend": () => Response.json(LEGEND),
      "/symbols/counts": () => Response.json(COUNTS),
      "/symbols/object-types": () => Response.json(CHOICES),
    });
    renderAt(`/bids/${BID}/symbols`);

    const unmapped = await screen.findByRole("list", {
      name: "Unmapped symbols",
    });
    expect(within(unmapped).getByText(/UNK-01/)).toBeInTheDocument();
    expect(within(unmapped).getByText("3")).toBeInTheDocument();
    expect(within(unmapped).getByText("No legend entry")).toBeInTheDocument();
    const counted = screen.getByRole("list", { name: "Counted objects" });
    expect(within(counted).getByText("Sprinkler, pendent")).toBeInTheDocument();
    expect(within(counted).queryByText(/UNK-01/)).not.toBeInTheDocument();
  });

  // req: FR-VIS-02
  it("shows who proposed a mapping and how sure it was, and confirms it", async () => {
    const calls = stubApi({
      "/symbols/legend": () => Response.json(LEGEND),
      "/symbols/counts": () => Response.json(COUNTS),
      "/symbols/object-types": () => Response.json(CHOICES),
      "/confirm": () =>
        Response.json(
          mapping({ state: "confirmed", confirmed_by: "Ethan Lim" }),
        ),
    });
    renderAt(`/bids/${BID}/symbols`);

    expect(
      await screen.findByText(/proposed by model claude-opus-5, 82% sure/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    const confirm = calls.find((call) =>
      call.url.endsWith(`/mappings/${LINEAGE}/confirm`),
    );
    expect(confirm?.method).toBe("POST");
    expect(confirm?.body).toBe("{}");
  });

  // req: FR-VIS-02
  it("corrects a proposal to another type", async () => {
    const calls = stubApi({
      "/symbols/legend": () => Response.json(LEGEND),
      "/symbols/counts": () => Response.json(COUNTS),
      "/symbols/object-types": () => Response.json(CHOICES),
      "/confirm": () => Response.json(mapping({ state: "confirmed" })),
    });
    renderAt(`/bids/${BID}/symbols`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Correct" }),
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Object type"),
      "sprinkler_pendent",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    const confirm = calls.find((call) => call.url.endsWith("/confirm"));
    expect(JSON.parse(confirm?.body ?? "{}")).toEqual({
      object_type: "sprinkler_pendent",
    });
  });
});

describe("the library page", () => {
  // req: FR-ADM-02
  it("opens a type's version history", async () => {
    signedInAs({
      name: "Sam Ong",
      preferred_username: "senior.estimator@firebid.test",
      roles: ["senior_estimator"],
    });
    const version = (number: number, label: string) => ({
      key: "sprinkler_pendent",
      version: number,
      label,
      category: "sprinkler",
      measure: "count",
      attribute_schema: { k_factor: { type: "number" } },
      deprecated: false,
      change_note: number === 2 ? "house style" : "seeded",
      changed_by: number === 2 ? "Sam Ong" : "platform",
      created_at: "2026-09-28T01:00:00+00:00",
    });
    stubApi({
      "/library/object-types/sprinkler_pendent/history": () =>
        Response.json([
          version(1, "Sprinkler, pendent"),
          version(2, "Pendent sprinkler head"),
        ]),
      "/library/object-types": () =>
        Response.json([version(2, "Pendent sprinkler head")]),
    });
    renderAt("/library");

    await userEvent.click(await screen.findByRole("button", { name: "v2" }));

    const history = await screen.findByRole("list", {
      name: "Version history",
    });
    expect(within(history).getByText(/Sprinkler, pendent/)).toBeInTheDocument();
    expect(within(history).getByText(/Sam Ong/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Deprecate" }),
    ).toBeInTheDocument();
  });

  // req: FR-ADM-02
  it("offers no edits to someone who may not change the library", async () => {
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    });
    stubApi({
      "/library/object-types": () =>
        Response.json([
          {
            key: "gate_valve",
            version: 1,
            label: "Gate valve",
            category: "valve",
            measure: "count",
            attribute_schema: {},
            deprecated: false,
            change_note: null,
            changed_by: "platform",
            created_at: "2026-09-28T01:00:00+00:00",
          },
        ]),
    });
    renderAt("/library");

    expect(await screen.findByText("Gate valve")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Deprecate" }),
    ).not.toBeInTheDocument();
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The workbench's queue and item panel: riskiest first, one click from a queue line to its
 * evidence, and accept and reject through the API (reject only with a reason).
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const SHEET = "22222222-0000-4000-8000-000000000001";

function item(id: string, human: string, description: string, overrides = {}) {
  return {
    id,
    human_id: human,
    version: 1,
    item_type: "sprinkler_pendent",
    classification: "sprinkler",
    description,
    attributes: { finish: { value: "chrome", source: "specification" } },
    unit: "no",
    net_quantity: "16.000",
    allowance_percent: "0.000",
    allowance_quantity: "0.000",
    quantity_with_allowance: "16.000",
    length_mm: null,
    level: "L05",
    zone: null,
    grid_from: "Grid A1–B2",
    grid_to: "Grid D3–E4",
    calculation_method: "count",
    rule_derived: false,
    rule: null,
    manual: false,
    manual_by: null,
    manual_at: null,
    confidence: 0.8,
    state: "proposed",
    duplicate_group_id: null,
    sources: [{ sheet_id: SHEET, sheet_number: "FP-L05-201", revision: "R01" }],
    note: null,
    supersedes_id: null,
    evidence_missing: [],
    evidence_boxes: [{ sheet_id: SHEET, box: [40, 80, 160, 200] }],
    ...overrides,
  };
}

const QUEUE = [
  {
    item: item("11111111-0000-4000-8000-000000000001", "QTO-000005", "Gate valve, DN150", {
      item_type: "gate_valve",
      classification: "valve",
      net_quantity: "1.000",
    }),
    risk: 12.5,
    impact: 25,
    system: "sprinkler",
    sheet_ids: [SHEET],
  },
  {
    item: item("11111111-0000-4000-8000-000000000002", "QTO-000011", "Sprinkler, pendent"),
    risk: 3.2,
    impact: 16,
    system: "sprinkler",
    sheet_ids: [SHEET],
  },
];

const ACTION = {
  id: "33333333-0000-4000-8000-000000000001",
  kind: "accept",
  actor: "Esther",
  reason_code: null,
  note: null,
  item_ids: [],
  detection_ids: [],
  count: 1,
  created_at: "2026-09-28T01:00:00+00:00",
  undone: false,
  undoes_id: null,
};

const TYPES = [
  { key: "flow_switch", label: "Flow switch", category: "device", attribute_schema: {}, measure: "count" },
  { key: "pipe", label: "Pipe", category: "pipe", attribute_schema: { nominal_diameter_mm: {} }, measure: "length" },
];

function view(status: string) {
  return {
    id: "44444444-0000-4000-8000-000000000001",
    kind: "plan",
    extent: [0, 0, 420, 297],
    scale_status: status,
    denominator: status === "verified" ? 100 : null,
    measurable: status === "verified",
  };
}

const COVERAGE = {
  items_total: 13,
  items_verified: 11,
  items_percent: 84.62,
  value_total: 200,
  value_verified: 150,
  value_percent: 75,
  value_basis: "weighted by item class (no rates yet)",
  policy_percent: 100,
  met: false,
};

const BLOCKED = {
  clear: false,
  unresolved_groups: [{ id: "g1", kind: "enlarged_plan", level: "L05", reason: "repeats" }],
  incomplete_items: [
    { id: "11111111-0000-4000-8000-000000000002", human_id: "QTO-000011", missing: ["location.level"] },
  ],
  pending_work: [{ task: "detection.run", status: "todo", jobs: 1 }],
  coverage: COVERAGE,
  unmapped_symbols: [
    { symbol_key: "shape:abc", description: null, instances: 14, status: "no legend", sheets: [SHEET], mapping_lineage_id: null },
  ],
};

const CLEAR = {
  clear: true,
  unresolved_groups: [],
  incomplete_items: [],
  pending_work: [],
  coverage: { ...COVERAGE, items_verified: 13, items_percent: 100, met: true },
  unmapped_symbols: [],
};

function stubs(views = [view("verified")], g1: object = BLOCKED) {
  return stubApi({
    "/qto/g1/approve": () =>
      Response.json(
        { id: "a1", gate: "G1", decision: "approved", decided_at: "2026-09-28T02:00:00Z", snapshot_hash: "x" },
        { status: 201 },
      ),
    "/qto/g1": () => Response.json(g1),
    "/review/coverage": () => Response.json((g1 as { coverage: object }).coverage),
    "/qto/duplicates": () => Response.json([]),
    "/qto/sheets": () =>
      Response.json([
        {
          sheet_id: SHEET,
          sheet_number: "FP-L05-201",
          revision: "R01",
          title: null,
          level: "L05",
          width_mm: 420,
          height_mm: 297,
          views,
        },
      ]),
    "/symbols/object-types": () => Response.json(TYPES),
    [`/sheets/${SHEET}`]: () => Response.json({ tile_source: null }),
    "/qto/overlay": () => Response.json([]),
    "/review/queue": () => Response.json(QUEUE),
    "/review/reasons": () =>
      Response.json([
        { code: "wrong_quantity", label: "The count or length is wrong" },
        { code: "not_in_scope", label: "Outside this package's scope" },
      ]),
    "/review/actions": () => Response.json([]),
    "/evidence": () =>
      Response.json({
        detection_method: "cad_entity",
        calculation_method: "count",
        calculation_note: "count of 16 detections on FP-L05-201",
      }),
    "/review/accept": () => Response.json(ACTION),
    "/review/reject": () => Response.json({ ...ACTION, kind: "reject" }),
  });
}

beforeAll(() => {
  // jsdom lays nothing out; give the virtualised list a height so it renders rows.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 600,
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 400,
  });
});

describe("the workbench", () => {
  beforeEach(() =>
    signedInAs({
      name: "Esther Tan",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-REV-02
  it("lists the queue riskiest first", async () => {
    stubs();
    renderAt(`/bids/${BID}/workbench`);

    const rows = await screen.findAllByRole("row");
    expect(rows.map((r) => r.getAttribute("data-human-id"))).toEqual([
      "QTO-000005",
      "QTO-000011",
    ]);
  });

  // req: FR-REV-01
  // req: NFR-10
  it("opens a queue line's evidence in one click", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/workbench`);

    const [, pendent] = await screen.findAllByRole("row");
    await userEvent.click(pendent!);

    const panel = await screen.findByRole("region", { name: "Item QTO-000011" });
    expect(within(panel).getByText("FP-L05-201 rev R01")).toBeInTheDocument();
    expect(await within(panel).findByText(/count of 16 detections/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/items/11111111-0000-4000-8000-000000000002/evidence"))).toBe(true);
  });

  // req: FR-REV-03
  it("accepts in bulk, and rejects only with a reason", async () => {
    const calls = stubs();
    renderAt(`/bids/${BID}/workbench`);

    await screen.findAllByRole("row");
    await userEvent.click(screen.getByRole("button", { name: /Select page/ }));
    await userEvent.click(screen.getByRole("button", { name: /^Accept 2$/ }));

    const accept = calls.find((c) => c.url.endsWith("/review/accept"));
    expect(JSON.parse(accept!.body!).item_ids).toHaveLength(2);

    await userEvent.click(screen.getByRole("checkbox", { name: "Select QTO-000005" }));
    const reject = screen.getByRole("button", { name: /^Reject 1$/ });
    expect(reject).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Reason to reject"), "not_in_scope");
    await userEvent.click(reject);

    const rejected = calls.find((c) => c.url.endsWith("/review/reject"));
    expect(JSON.parse(rejected!.body!)).toMatchObject({ reason_code: "not_in_scope" });
  });

  // req: FR-QTO-11
  it("offers the manual tools only where a view's scale can be trusted, and says why", async () => {
    stubs([view("unverified")]);
    renderAt(`/bids/${BID}/workbench`);

    await userEvent.click(await screen.findByText("Add what was missed"));
    const tools = await screen.findByRole("region", { name: "Manual takeoff" });
    expect(
      await within(tools).findByText(/No view on FP-L05-201 has a verified or calibrated scale \(unverified\)/),
    ).toBeInTheDocument();
    expect(within(tools).queryByRole("button", { name: /Start/ })).not.toBeInTheDocument();
  });

  // req: FR-QTO-11
  it("picks the type from the library, by how it is taken off", async () => {
    stubs();
    renderAt(`/bids/${BID}/workbench`);

    await userEvent.click(await screen.findByText("Add what was missed"));
    const tools = await screen.findByRole("region", { name: "Manual takeoff" });
    const type = within(tools).getByLabelText("Type");
    await screen.findAllByRole("row");
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toContain("Flow switch");
    await userEvent.click(within(tools).getByLabelText("Length"));
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toContain("Pipe");
    await userEvent.selectOptions(type, "pipe");
    expect(within(tools).getByLabelText("nominal diameter mm")).toBeInTheDocument();
    await userEvent.click(within(tools).getByRole("button", { name: "Start measuring" }));
    const measuring = await screen.findByRole("region", { name: "Manual takeoff" });
    expect(await within(measuring).findByText(/Measuring Pipe/)).toBeInTheDocument();
  });

  // req: FR-REV-04
  it("keeps G1 disabled and lists every blocker, with where to resolve it", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    stubs();
    renderAt(`/bids/${BID}/workbench`);

    await userEvent.click(await screen.findByRole("button", { name: "Coverage & G1" }));

    const blockers = await screen.findByRole("list", { name: "What blocks G1" });
    const items = within(blockers).getAllByRole("listitem").map((i) => i.textContent);
    expect(items).toEqual([
      expect.stringContaining("Coverage is 84.62%, the policy is 100%"),
      expect.stringContaining("1 unresolved duplicate group"),
      expect.stringContaining("1 symbol type(s) on Current sheets nobody has named"),
      expect.stringContaining("QTO-000011 has an incomplete evidence record (location.level)"),
      expect.stringContaining("still being read or detected"),
    ]);
    expect(screen.getByRole("button", { name: /Approve G1/ })).toBeDisabled();
    expect(screen.getByRole("meter", { name: "Items verified" })).toHaveAttribute("aria-valuenow", "84.62");

    await userEvent.click(within(blockers).getByRole("button", { name: "Resolve them" }));
    expect(await screen.findByText("No repeats found across these sheets.")).toBeInTheDocument();
  });

  // req: FR-REV-04
  it("lets only a senior estimator approve G1 once nothing blocks it", async () => {
    stubs(undefined, CLEAR);
    renderAt(`/bids/${BID}/workbench`);
    await userEvent.click(await screen.findByRole("button", { name: "Coverage & G1" }));
    expect(await screen.findByText("Only a Senior Estimator approves G1.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve G1/ })).toBeDisabled();
  });

  // req: FR-REV-04
  it("approves G1 for a senior estimator", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const calls = stubs(undefined, CLEAR);
    renderAt(`/bids/${BID}/workbench`);
    await userEvent.click(await screen.findByRole("button", { name: "Coverage & G1" }));

    const approve = await screen.findByRole("button", { name: /Approve G1/ });
    await waitFor(() => expect(approve).toBeEnabled());
    await userEvent.click(approve);

    expect(await screen.findByText(/G1 approved at/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/qto/g1/approve") && c.method === "POST")).toBe(true);
  });
});

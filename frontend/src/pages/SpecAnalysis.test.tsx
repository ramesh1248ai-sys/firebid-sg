import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The specification page's analysis: obligations, issues and the scope matrix, each one
 * click from the clause it cites, and an issue from its sheet too.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const CLAUSE = "77777777-0000-4000-8000-000000000061";
const SHEET = "22222222-0000-4000-8000-000000000001";

const OBLIGATION = {
  id: "aaaaaaaa-0000-4000-8000-000000000001",
  document_revision_id: "99999999-0000-4000-8000-000000000002",
  clause_id: CLAUSE,
  clause_number: "6.1",
  system: "fire_protection",
  category: "testing",
  summary: "All pipework shall be hydrostatically tested at 14 bar for 2 hours.",
  quantities: { pressure: 14, pressure_unit: "bar", duration_hours: 2 },
  quote: "All pipework shall be hydrostatically tested at 14 bar for 2 hours.",
  method: "rule",
  confidence: 0.85,
  citation_ok: true,
  citation_reason: "the clause states it",
  state: "proposed",
  decided_by: null,
  decided_at: null,
  note: null,
};

const ISSUE = {
  id: "bbbbbbbb-0000-4000-8000-000000000001",
  category: "conflict",
  rule: "conflict:pipe_material",
  severity: "high",
  title:
    "Pipe material: the specification says galvanised steel, sheet FP-B1-201 says black steel",
  system: "sprinkler",
  spec_ref: {
    clause: "2.1.3",
    clause_id: CLAUSE,
    quote: "All sprinkler pipework within the basement car park shall be hot-dip galvanised.",
    revision: "B",
  },
  drawing_ref: {
    sheet_id: SHEET,
    sheet_number: "FP-B1-201",
    revision: "R01",
    note: "ALL SPRINKLER PIPEWORK TO BE BLACK STEEL",
  },
  detail: { what: "pipe_material", specified: "galvanised_steel", drawn: "black_steel" },
  state: "open",
  decided_by: null,
  decided_at: null,
  note: null,
};

function row(key: string, label: string, status: string, clause: string | null) {
  return {
    id: `cccccccc-0000-4000-8000-0000000000${key.length.toString().padStart(2, "0")}`,
    system: "sprinkler",
    kind: "interface",
    key,
    label,
    status,
    proposed_status: status,
    document_revision_id: "99999999-0000-4000-8000-000000000002",
    clause_id: clause ? CLAUSE : null,
    clause_number: clause,
    quote: clause ? "Power supply shall be provided by the electrical contractor." : null,
    reason: clause ? "the clause says whose it is" : "the specification does not mention it",
    source: "rule",
    note: null,
    edited_by: null,
    confirmed_by: null,
    confirmed_at: null,
  };
}

const MATRIX = [
  row("power_supply", "Power supply to fire pumps and control panels", "by_others", "7.1"),
  row("drainage", "Drainage for test and drain points", "unclear", null),
];

function stubs(extra: Record<string, (call: { body: unknown }) => Response> = {}) {
  return stubApi({
    "/spec/attributes": () => Response.json([]),
    "/spec/obligations": () => Response.json([OBLIGATION]),
    "/spec/issues": () => Response.json([ISSUE]),
    "/spec/scope-matrix": () => Response.json(MATRIX),
    [`/spec/clauses/${CLAUSE}`]: () =>
      Response.json({
        id: CLAUSE,
        number: "6.1",
        heading: "Testing",
        text: OBLIGATION.quote,
        anchor: { paragraph: 30 },
        system: "fire_protection",
        document_id: "99999999-0000-4000-8000-000000000001",
        document_title: "PARTICULAR SPECIFICATION",
        revision_label: "B",
      }),
    ...extra,
  });
}

describe("the specification analysis", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-SPEC-02
  it("lists each obligation with what it states, one click from its clause", async () => {
    stubs();
    renderAt(`/bids/${BID}/specification`);

    const table = await screen.findByRole("table", { name: "Obligations" });
    expect(within(table).getByText("testing")).toBeInTheDocument();
    expect(within(table).getByText("14 bar, 2 hours")).toBeInTheDocument();
    expect(within(table).getByText("to confirm · rule")).toBeInTheDocument();

    await userEvent.click(within(table).getByRole("button", { name: "6.1" }));

    const panel = await screen.findByRole("complementary", { name: "Cited clause" });
    expect(panel.querySelector("mark")?.textContent).toBe(OBLIGATION.quote);
  });

  // req: FR-SPEC-02
  it("records a person's confirmation", async () => {
    const calls = stubs({
      [`/spec/obligations/${OBLIGATION.id}/decide`]: () =>
        Response.json({ ...OBLIGATION, state: "verified", decided_by: "Ethan Lim" }),
    });
    renderAt(`/bids/${BID}/specification`);

    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    const sent = calls.find((call) => call.url.includes("/decide"));
    expect(JSON.parse(String(sent?.body))).toEqual({ decision: "confirm" });
  });

  // req: FR-SPEC-03
  it("shows an issue with both sides cited, and takes the reader to each", async () => {
    stubs();
    renderAt(`/bids/${BID}/specification`);

    await userEvent.click(await screen.findByRole("button", { name: "Issues (1)" }));

    const list = screen.getByRole("list", { name: "Issues" });
    expect(within(list).getByText(ISSUE.title)).toBeInTheDocument();
    expect(within(list).getByText("high")).toBeInTheDocument();
    expect(within(list).getByText("“ALL SPRINKLER PIPEWORK TO BE BLACK STEEL”")).toBeInTheDocument();
    const sheet = within(list).getByRole("link", { name: "FP-B1-201 R01" });
    expect(sheet.getAttribute("href")).toBe(`/bids/${BID}/sheets/${SHEET}`);

    await userEvent.click(within(list).getByRole("button", { name: "clause 2.1.3" }));

    expect(await screen.findByRole("complementary", { name: "Cited clause" })).toBeInTheDocument();
  });

  // req: FR-SPEC-03
  it("dismisses an issue only with a reason", async () => {
    const calls = stubs({
      [`/spec/issues/${ISSUE.id}/decide`]: () => Response.json({ ...ISSUE, state: "dismissed" }),
    });
    renderAt(`/bids/${BID}/specification`);
    await userEvent.click(await screen.findByRole("button", { name: "Issues (1)" }));

    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: /is not an issue/ }),
      "superseded by Addendum 2",
    );
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    const sent = calls.find((call) => call.url.includes("/decide"));
    expect(JSON.parse(String(sent?.body))).toEqual({ decision: "dismiss", note: "superseded by Addendum 2" });
  });

  // req: FR-SPEC-04
  it("shows the scope matrix with a status and clause on each row, and lets a person change one", async () => {
    const calls = stubs({
      "/spec/scope-matrix/rows/": () => Response.json({ ...MATRIX[1], status: "excluded" }),
    });
    renderAt(`/bids/${BID}/specification`);
    await userEvent.click(await screen.findByRole("button", { name: "Scope matrix" }));

    const table = screen.getByRole("table", { name: "Scope of sprinkler" });
    const [power, drainage] = within(table).getAllByRole("row").slice(1);
    expect(within(power!).getByRole("combobox")).toHaveValue("by_others");
    expect(within(power!).getByRole("button", { name: "7.1" })).toBeInTheDocument();
    expect(within(drainage!).getByRole("combobox")).toHaveValue("unclear");
    expect(within(drainage!).getByText("the specification does not mention it")).toBeInTheDocument();
    expect(screen.getByText("1 unclear · not confirmed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export to Excel" })).toBeInTheDocument();

    await userEvent.selectOptions(within(drainage!).getByRole("combobox"), "excluded");

    const sent = calls.find((call) => call.url.includes("/scope-matrix/rows/"));
    expect(JSON.parse(String(sent?.body))).toEqual({ status: "excluded" });
  });
});

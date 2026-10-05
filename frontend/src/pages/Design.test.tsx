import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/** Design development: a sheet's criteria, a person's confirmation, and what was proposed. */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const CONCEALED = {
  key: "note_1",
  title: "(ORDINARY HAZARD GROUP III)",
  max_area_m2: 12,
  max_spacing_mm: [4000, 3000],
  source: "sheet FP-L10-01 notes: 'MAXIMUM SPACING: (4M X 3M)'",
  k_factor: "5.6 (80)",
  response: null,
};
const DEFAULT = {
  key: "oh_default",
  title: "Ordinary Hazard (SS CP 52), design rules default",
  max_area_m2: 12,
  max_spacing_mm: [4000, 4000],
  source: "design rules default (to be confirmed)",
  k_factor: null,
  response: null,
};

function sheet(overrides: Record<string, unknown>) {
  return {
    id: "d0000000-0000-4000-8000-000000000001",
    sheet_id: "50000000-0000-4000-8000-000000000001",
    sheet_number: "FP-L10-01",
    level: "L10",
    view_id: "70000000-0000-4000-8000-000000000001",
    state: "proposed",
    note: null,
    design_intent: true,
    intent_quote: "THE DESIGN INTENT CONVEYED IN THIS DRAWING SHALL CONSTITUTE THE MINIMUM REQUIREMENT.",
    drawn_heads: 0,
    criteria: [CONCEALED, DEFAULT],
    criterion: null,
    scope: null,
    scope_source: null,
    match_lines: [],
    rule_version: null,
    totals: {},
    spaces: [],
    laid_out_at: null,
    confirmed_by: null,
    confirmed_at: null,
    ...overrides,
  };
}

const DRAWN = sheet({
  id: "d0000000-0000-4000-8000-000000000002",
  sheet_id: "50000000-0000-4000-8000-000000000002",
  sheet_number: "FP-L05-201",
  level: "L05",
  design_intent: false,
  intent_quote: null,
  drawn_heads: 48,
  criteria: [DEFAULT],
});
const BLOCKED = sheet({
  id: "d0000000-0000-4000-8000-000000000003",
  sheet_id: "50000000-0000-4000-8000-000000000003",
  sheet_number: "FP-L11-01",
  level: "L11",
  state: "blocked",
  note: "no plan view on this sheet has a verified or calibrated scale (FR-VIS-05)",
});

describe("design development", () => {
  // req: FR-DSN-01
  it("offers the design-intent sheets with no heads drawn, and confirms the chosen criterion", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const calls = stubApi({
      "/design/confirm": () => Response.json({ confirmed: ["50000000-0000-4000-8000-000000000001"], skipped: {} }),
      "/design": () =>
        Response.json({ rule_version: 1, rule_status: "to be confirmed", sheets: [sheet({}), DRAWN, BLOCKED] }),
    });
    renderAt(`/bids/${BID}/design`);

    const table = await screen.findByRole("table", { name: "Plan sheets" });
    // The seeded rules say they are unconfirmed.
    expect(screen.getByRole("note")).toHaveTextContent("Design rules version 1 (to be confirmed)");
    // Only the design-intent sheet with nothing drawn is selected to start with.
    expect(within(table).getByLabelText("Select FP-L10-01")).toBeChecked();
    expect(within(table).getByLabelText("Select FP-L05-201")).not.toBeChecked();
    expect(within(table).getByLabelText("Select FP-L11-01")).toBeDisabled();
    const drawn = within(table).getByText("FP-L05-201").closest("tr")!;
    expect(drawn).toHaveTextContent("a layout would count these twice");
    const blocked = within(table).getByText("FP-L11-01").closest("tr")!;
    expect(blocked).toHaveTextContent("FR-VIS-05");

    await userEvent.click(screen.getByRole("button", { name: "Confirm and propose a layout (1)" }));

    expect(await screen.findByRole("status")).toHaveTextContent("1 sheet confirmed");
    const sent = calls.find((call) => call.url.endsWith("/design/confirm"));
    expect(JSON.parse(sent!.body!)).toEqual({
      sheet_ids: ["50000000-0000-4000-8000-000000000001"],
      key: "note_1",
    });
  });

  // req: FR-DSN-03
  it("shows what was proposed, what was left out and why", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const confirmed = sheet({
      state: "confirmed",
      criterion: CONCEALED,
      rule_version: 1,
      confirmed_by: "Sam Lim",
      totals: { heads: 41, range_pipe_m: { "25": 60.5, "32": 12.0 }, area_m2: 311.0, remote_rows: 2 },
      spaces: [
        { index: 0, kind: "room", name: "WARD A", area_m2: 80, heads: 12, pitch_mm: [2800, 2800], omitted_by: null, note: null, box: [0, 0, 1, 1] },
        { index: 1, kind: "room", name: "LIFT 4", area_m2: 9, heads: 0, pitch_mm: [4000, 3000], omitted_by: "\\bLIFT\\b", note: null, box: [0, 0, 1, 1] },
      ],
    });
    stubApi({
      "/design": () => Response.json({ rule_version: 1, rule_status: "to be confirmed", sheets: [confirmed] }),
    });
    renderAt(`/bids/${BID}/design`);

    const table = await screen.findByRole("table", { name: "Plan sheets" });
    const row = within(table).getByText("FP-L10-01").closest("tr")!;
    expect(row).toHaveTextContent("41");
    expect(row).toHaveTextContent("72.5 m");
    expect(row).toHaveTextContent("by Sam Lim");

    await userEvent.click(within(row).getByRole("button", { name: "FP-L10-01" }));

    const omitted = screen.getByRole("list", { name: "Omitted spaces" });
    expect(omitted).toHaveTextContent("LIFT 4, 9 m²");
    expect(table).toHaveTextContent("2 rows are fed by an allowance");
    expect(table).toHaveTextContent("sheet FP-L10-01 notes: 'MAXIMUM SPACING: (4M X 3M)'");
  });

  // req: FR-DSN-06
  it("shows the match line a sheet's layout stops at, and lets a person take the other side", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const lined = sheet({
      scope: [[0, 0], [175, 0], [175, 300], [0, 300]],
      scope_source: "match_lines",
      match_lines: [
        {
          label: "MATCH LINE - SEE DWG FP-L10-02",
          other_sheet: "FP-L10-02",
          line: [[175, 0], [175, 300]],
          side: 1,
          proposed_side: 1,
          reason: "the side with more of the pipework drawn",
        },
        { label: "MATCH LINE", other_sheet: null, line: null, side: null, proposed_side: null, reason: null },
      ],
    });
    const calls = stubApi({
      "/scope": () => new Response(null, { status: 204 }),
      "/design": () => Response.json({ rule_version: 1, rule_status: "to be confirmed", sheets: [lined] }),
    });
    renderAt(`/bids/${BID}/design`);

    const table = await screen.findByRole("table", { name: "Plan sheets" });
    const row = within(table).getByText("FP-L10-01").closest("tr")!;
    expect(row).toHaveTextContent("Limited to its side of its match lines");
    await userEvent.click(within(row).getByRole("button", { name: "FP-L10-01" }));

    const lines = screen.getByRole("list", { name: "Match lines" });
    expect(lines).toHaveTextContent("continues on FP-L10-02");
    expect(lines).toHaveTextContent("this sheet's side: the side with more of the pipework drawn");
    expect(lines).toHaveTextContent("its line was not found, so it does not limit the layout");
    expect(table).toHaveTextContent("so the shared floor is designed once");

    await userEvent.click(within(lines).getByRole("button", { name: "Take the other side" }));

    expect(await screen.findByRole("status")).toHaveTextContent("The sheet's scope was changed");
    const sent = calls.find((call) => call.url.endsWith("/scope"));
    expect(sent!.method).toBe("PUT");
    expect(JSON.parse(sent!.body!)).toEqual({ follow_match_lines: false, sides: { "0": -1 } });

    await userEvent.click(screen.getByRole("button", { name: "Use the whole sheet" }));
    const whole = calls.filter((call) => call.url.endsWith("/scope")).at(-1);
    expect(JSON.parse(whole!.body!)).toEqual({ follow_match_lines: false });
  });

  // req: FR-DSN-05
  it("offers a confirmed sheet's layout as a PDF and a DXF, and says what the download is", async () => {
    signedInAs({ name: "Sam Lim", preferred_username: "senior.estimator@firebid.test", roles: ["senior_estimator"] });
    const confirmed = sheet({
      state: "confirmed",
      criterion: CONCEALED,
      rule_version: 1,
      confirmed_by: "Sam Lim",
      totals: { heads: 41, range_pipe_m: { "25": 60.5 }, area_m2: 311.0 },
    });
    const calls = stubApi({
      "/export/status": () =>
        Response.json([
          { format: "pdf", state: "none", bytes: null, made_at: null, reason: null },
          { format: "dxf", state: "ready", bytes: 9, made_at: "2026-10-05T02:00:00+00:00", reason: null },
        ]),
      "/export": (call) =>
        call.method === "POST"
          ? Response.json(
              { format: "dxf", state: "queued", bytes: null, made_at: null, reason: null },
              { status: 202 },
            )
          : new Response("0\nSECTION", {
              headers: {
                "content-type": "application/dxf",
                "content-disposition": 'attachment; filename="FP-L10-01-proposed-layout.dxf"',
              },
            }),
      "/design": () =>
        Response.json({ rule_version: 1, rule_status: "to be confirmed", sheets: [confirmed, sheet({ sheet_id: "50000000-0000-4000-8000-000000000009", id: "d0000000-0000-4000-8000-000000000009", sheet_number: "FP-L12-01" })] }),
    });
    URL.createObjectURL = () => "blob:layout";
    URL.revokeObjectURL = () => undefined;
    renderAt(`/bids/${BID}/design`);

    const table = await screen.findByRole("table", { name: "Plan sheets" });
    await userEvent.click(within(table).getByRole("button", { name: "FP-L10-01" }));

    expect(table).toHaveTextContent("For estimation only: not for construction");
    expect(table).toHaveTextContent("the platform sends it nowhere");
    expect(screen.getByRole("button", { name: "Download the layout as PDF" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Download the layout as DXF" }));

    // Asked for, waited for, then downloaded: three calls, in that order.
    expect(await screen.findByRole("status")).toHaveTextContent("Making the DXF of FP-L10-01");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("The DXF of FP-L10-01 was downloaded"), {
      timeout: 6000,
    });
    const made = calls.filter((call) => call.url.includes("/export"));
    expect(made.map((call) => call.method)).toEqual(["POST", "GET", "GET"]);
    expect(made[0]!.url).toContain("/design/sheets/50000000-0000-4000-8000-000000000001/export?format=dxf");
    expect(made[1]!.url).toContain("/export/status");
    expect(made[2]!.url).toContain("/export?format=dxf");

    // A sheet with no layout yet offers nothing to download.
    await userEvent.click(within(table).getByRole("button", { name: "FP-L12-01" }));
    expect(screen.queryByRole("button", { name: "Download the layout as PDF" })).toBeNull();
  });
});

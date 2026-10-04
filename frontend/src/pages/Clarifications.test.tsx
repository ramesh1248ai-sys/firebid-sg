import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { type Call, renderAt, stubApi } from "@/test/harness";

/**
 * The clarifications page. What matters: a draft shows the evidence it rests on and its
 * options as recommendations; engineering content says the Design Manager must approve it;
 * the register shows what is due; a group is one clarification only once confirmed; and
 * what is unresolved at submission is shown as a proposed qualification, linked back.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const TC1 = "aaaaaaaa-0000-4000-8000-000000000001";
const SHEET = "22222222-0000-4000-8000-000000000001";

const MATERIAL = {
  kind: "spec_issue",
  ref: "issue-material",
  subject: "Pipe material: the specification says galvanised steel, sheet FP-B1-201 says black steel",
  problem: "The two do not agree on the pipe material. Please confirm which governs.",
  evidence: [
    { kind: "clause", label: "Specification clause 2.1.3", quote: "shall be hot-dip galvanised", link: null, revision: "B" },
    { kind: "sheet", label: "Drawing FP-B1-201", quote: "TO BE BLACK STEEL", link: null, revision: "R01" },
  ],
  system: "sprinkler",
  level_grid: "",
  sheets: [{ sheet_number: "FP-B1-201", revision: "R01", sheet_id: SHEET }],
  options: ["the specification governs (galvanised steel)"],
  topic: "pipe_material",
};
const JOINING = {
  ...MATERIAL,
  ref: "issue-joining",
  subject: "Joining method: the specification says grooved, sheet FP-B1-201 says screwed",
  topic: "joining_method",
};
const FLOW = {
  kind: "boq_variance",
  ref: "client:Bill 1!26",
  subject: "Bill item not found on the drawings: Flow switch, 150 mm",
  problem: "The tender drawings show nothing it measures.",
  evidence: [{ kind: "client_boq_line", label: "Client bill Bill 1!26", quote: "C3: 1 nr", link: null, revision: null }],
  system: "",
  level_grid: "",
  sheets: [],
  options: [],
  topic: "quantity",
};

function clarification(overrides: Record<string, unknown> = {}) {
  return {
    id: TC1,
    kind: "tender_clarification",
    number: "TC-001",
    subject: MATERIAL.subject,
    project: "Marina Bay Commercial Tower",
    level_grid: "B1",
    sheets: MATERIAL.sheets,
    problem: "Specification clause 2.1.3 states galvanised; FP-B1-201 notes black steel. Please confirm which governs.",
    evidence: MATERIAL.evidence,
    options: [
      { text: "Recommendation: the specification governs (galvanised steel)", recommendation: true },
    ],
    cost_impact: "",
    programme_impact: "",
    required_reviewer: "design_manager",
    engineering_content: true,
    engineering_reason: "it concerns pipe material",
    qp_input_needed: false,
    state: "internal_review",
    state_label: "Internal review",
    due_at: "2026-10-18T09:00:00Z",
    overdue: true,
    drafting: { method: "rules" },
    design_approved_by: null,
    design_approved_at: null,
    design_note: null,
    approved_by: null,
    approved_at: null,
    issued_by: null,
    issued_at: null,
    responded_at: null,
    response_summary: null,
    response_document_id: null,
    impact_task_id: null,
    impact_outcome: null,
    impact_note: null,
    impact_detail: {},
    impact_by: null,
    sources: [{ kind: "spec_issue", ref: "issue-material" }],
    created_at: "2026-10-04T08:00:00Z",
    ...overrides,
  };
}

function register(rows: unknown[]) {
  return {
    clarification_cutoff: "2026-10-20T09:00:00Z",
    due_at: "2026-10-18T09:00:00Z",
    clarifications: rows,
    templates: [
      { key: "company_default", label: "Company default", headings: ["No.", "Subject"] },
      { key: "client_query_log", label: "Client query log (sample layout)", headings: ["Query No"] },
    ],
  };
}

function stubs(
  rows: unknown[] = [clarification()],
  extra: Record<string, (call: Call) => Response> = {},
) {
  return stubApi({
    "/clarifications/candidates": () =>
      Response.json({
        groups: [
          { key: "sprinkler|FP-B1-201", reason: "2 issues about sprinkler, FP-B1-201", candidates: [MATERIAL, JOINING] },
          { key: "boq_variance:client:Bill 1!26", reason: "on its own", candidates: [FLOW] },
        ],
        kinds: ["tender_clarification"],
      }),
    "/clarifications": (call) =>
      call.method === "POST"
        ? Response.json(clarification({ state: "draft", state_label: "Draft" }), { status: 201 })
        : Response.json(register(rows)),
    "/qualifications": () =>
      Response.json([
        {
          id: "bbbbbbbb-0000-4000-8000-000000000001",
          kind: "qualification",
          text: "Pipe material: tender clarification TC-001 was raised and is not resolved.",
          clarification_id: TC1,
          clarification_number: "TC-001",
          state: "proposed",
          decided_by: null,
          decided_at: null,
          note: null,
        },
      ]),
    ...extra,
  });
}

describe("the clarifications page", () => {
  beforeEach(() =>
    signedInAs({
      name: "Bella Ong",
      preferred_username: "bid.manager@firebid.test",
      roles: ["bid_manager"],
    }),
  );

  // req: FR-RFI-01
  it("is for tender clarifications, and offers no other kind", async () => {
    const calls = stubs([]);
    renderAt(`/bids/${BID}/clarifications`);

    expect(await screen.findByRole("heading", { name: "Tender clarifications" })).toBeVisible();
    expect(screen.queryByText(/construction rfi/i)).toBeNull();
    const list = await screen.findByRole("list", { name: "Candidates" });
    await userEvent.click(
      within(list).getByRole("button", { name: /Draft a clarification for Bill item not found/ }),
    );

    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!)).toEqual({
      candidates: [{ kind: "boq_variance", ref: "client:Bill 1!26" }],
      kind: "tender_clarification",
    });
  });

  // req: FR-RFI-02
  it("shows a draft with every field and the evidence it rests on", async () => {
    stubs();
    renderAt(`/bids/${BID}/clarifications`);

    await userEvent.click(await screen.findByRole("button", { name: "Open TC-001" }));

    const panel = screen.getByRole("region", { name: "Clarification TC-001" });
    expect(panel).toHaveTextContent("Marina Bay Commercial Tower");
    expect(panel).toHaveTextContent("B1");
    expect(within(panel).getByRole("link", { name: "FP-B1-201" })).toHaveAttribute(
      "href",
      `/bids/${BID}/sheets/${SHEET}`,
    );
    expect(panel).toHaveTextContent("rev R01");
    expect(within(panel).getByLabelText("Query")).toHaveValue(clarification().problem);
    const evidence = within(panel).getByRole("list", { name: "Evidence" });
    expect(within(evidence).getAllByRole("listitem")).toHaveLength(2);
    expect(evidence).toHaveTextContent("Specification clause 2.1.3 rev B");
    expect(evidence).toHaveTextContent("shall be hot-dip galvanised");
    expect(within(panel).getByLabelText("Potential cost impact")).toBeVisible();
    expect(within(panel).getByLabelText("Potential programme impact")).toBeVisible();
    expect(panel).toHaveTextContent("design manager");
    // The candidates say what each would cite, before anything is drafted.
    expect(screen.getByRole("list", { name: "Candidates" })).toHaveTextContent(
      "evidence: Client bill Bill 1!26",
    );
  });

  // req: FR-RFI-06
  it("labels options as recommendations and says the Design Manager must approve", async () => {
    const calls = stubs([clarification()], {
      [`/clarifications/${TC1}/transition`]: () =>
        Response.json(
          {
            detail:
              "clarification: 'approve to issue' refused: it has engineering, fire-safety or " +
              "structural content: the Design Manager's approval is missing",
          },
          { status: 409 },
        ),
    });
    renderAt(`/bids/${BID}/clarifications`);
    await userEvent.click(await screen.findByRole("button", { name: "Open TC-001" }));

    const panel = screen.getByRole("region", { name: "Clarification TC-001" });
    expect(within(panel).getByRole("list", { name: "Options" })).toHaveTextContent(
      "Recommendation: the specification governs (galvanised steel)",
    );
    expect(panel).toHaveTextContent("recommendations only: the client decides");
    expect(panel).toHaveTextContent(
      "Engineering content (it concerns pipe material): the Design Manager must approve it before issue",
    );
    await userEvent.click(within(panel).getByRole("button", { name: "Approve to issue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the Design Manager's approval is missing",
    );
    const post = calls.find((call) => call.url.endsWith("/transition"));
    expect(JSON.parse(post!.body!)).toEqual({ target: "approved_to_issue" });
  });

  // req: FR-RFI-04
  it("shows what is due against the cut-off, and records a response", async () => {
    const calls = stubs(
      [clarification({ state: "issued", state_label: "Issued", overdue: false })],
      { [`/clarifications/${TC1}/response`]: () => Response.json(clarification()) },
    );
    renderAt(`/bids/${BID}/clarifications`);

    expect(await screen.findByText(/cut-off of 20 Oct 2026; to be issued by 18 Oct 2026/)).toBeVisible();
    const table = screen.getByRole("table", { name: "Clarification register" });
    const row = within(table).getByText("TC-001").closest("tr")!;
    expect(row).toHaveTextContent("Issued");
    expect(row).toHaveTextContent("18 Oct 2026");
    await userEvent.click(within(row).getByRole("button", { name: "Open TC-001" }));
    const record = screen.getByRole("button", { name: "Record the response" });
    expect(record).toBeDisabled();
    await userEvent.type(screen.getByLabelText("The client's response"), "The specification governs.");
    await userEvent.click(record);

    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/response"))).toBe(true);
  });

  // req: FR-RFI-04
  it("marks a clarification past its due date", async () => {
    stubs();
    renderAt(`/bids/${BID}/clarifications`);

    const table = await screen.findByRole("table", { name: "Clarification register" });
    expect(within(table).getByText("TC-001").closest("tr")).toHaveTextContent(
      "18 Oct 2026 · overdue",
    );
  });

  // req: FR-RFI-07
  it("drafts one clarification from a group only when the group is confirmed", async () => {
    const calls = stubs([]);
    renderAt(`/bids/${BID}/clarifications`);

    const list = await screen.findByRole("list", { name: "Candidates" });
    expect(list).toHaveTextContent("Proposed as one clarification: 2 issues about sprinkler, FP-B1-201");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await userEvent.click(
      within(list).getByRole("button", { name: "Confirm the group and draft one" }),
    );

    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body!).candidates).toEqual([
      { kind: "spec_issue", ref: "issue-material" },
      { kind: "spec_issue", ref: "issue-joining" },
    ]);
  });

  // req: FR-RFI-07
  it("downloads the register in the chosen template", async () => {
    const calls = stubs([clarification()], {
      "/clarifications/export": () =>
        new Response(new Blob(["x"]), {
          headers: { "content-disposition": 'attachment; filename="BID clarifications.docx"' },
        }),
    });
    URL.createObjectURL = () => "blob:x";
    URL.revokeObjectURL = () => undefined;
    renderAt(`/bids/${BID}/clarifications`);

    await userEvent.selectOptions(await screen.findByLabelText("Template"), "client_query_log");
    await userEvent.selectOptions(screen.getByLabelText("Format"), "docx");
    await userEvent.click(screen.getByRole("button", { name: "Download the register" }));

    const request = calls.find((call) => call.url.includes("/clarifications/export"));
    expect(request!.method).toBe("GET");
    expect(request!.url).toContain("template=client_query_log");
    expect(request!.url).toContain("format=docx");
  });

  // req: FR-RFI-05
  it("shows what is unresolved at submission as a proposed qualification, linked back", async () => {
    const calls = stubs([clarification()], {
      "/clarifications/prepare-submission": () => Response.json([]),
    });
    renderAt(`/bids/${BID}/clarifications`);

    const list = await screen.findByRole("list", { name: "Qualifications" });
    const item = within(list).getByRole("listitem");
    expect(item).toHaveTextContent("tender clarification TC-001 was raised and is not resolved");
    expect(item).toHaveTextContent("from TC-001");
    expect(within(item).getByRole("button", { name: "Accept" })).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Prepare the submission (1 unresolved)" }),
    );

    expect(calls.some((call) => call.url.endsWith("/prepare-submission"))).toBe(true);
  });
});

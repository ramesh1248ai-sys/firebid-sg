import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The registers page. What matters: the estimator sees what stands between them and
 * "Register confirmed", and each of those things carries the action that clears it.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";

function drawing(overrides: Record<string, unknown> = {}) {
  return {
    revision_id: "11111111-0000-4000-8000-000000000001",
    sheet_id: "22222222-0000-4000-8000-000000000001",
    document_id: "33333333-0000-4000-8000-000000000001",
    filename: "FP-L05-201.pdf",
    sheet_number: "FP-L05-201",
    title: "FIRE SPRINKLER LAYOUT",
    revision: "R04",
    revision_date: "2026-06-12",
    discipline: "fire protection",
    level: "L05",
    zone: null,
    scale: "1:100",
    state: "current",
    confidence: 0.97,
    read_by: "text_layer",
    conflict_reason: null,
    addendum: null,
    content_class: "vector",
    quality_band: "high",
    manual_takeoff_recommended: false,
    ...overrides,
  };
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    conflicts: 0,
    unidentified: 0,
    unsure_types: 0,
    ready: true,
    confirmation: null,
    ...overrides,
  };
}

describe("the registers page", () => {
  beforeEach(() => signedInAs());

  // req: FR-DOC-03
  it("lists each drawing with its current revision first", async () => {
    stubApi({
      "/registers/status": () => Response.json(status()),
      "/registers/drawings": () =>
        Response.json([
          drawing(),
          drawing({ revision_id: "r03", revision: "R03", state: "superseded" }),
        ]),
    });
    renderAt(`/bids/${BID}/registers`);

    expect(await screen.findAllByText("FP-L05-201")).toHaveLength(2);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(
      rows.map((row) => within(row).getAllByRole("cell")[4]?.textContent),
    ).toEqual(["Current", "Superseded"]);
    expect(
      screen.getByRole("button", { name: "Register confirmed" }),
    ).toBeEnabled();
  });

  // req: FR-DOC-04
  it("says what blocks confirmation and offers the decision on a conflict", async () => {
    stubApi({
      "/registers/status": () =>
        Response.json(status({ conflicts: 1, ready: false })),
      "/registers/drawings": () =>
        Response.json([
          drawing({
            state: "conflict",
            conflict_reason:
              "The revision sources disagree: the filename says R03.",
          }),
        ]),
    });
    renderAt(`/bids/${BID}/registers`);

    expect(await screen.findByText(/1 in conflict/)).toBeInTheDocument();
    expect(screen.getByText(/the filename says R03/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Register confirmed" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  });

  // req: FR-DOC-04
  it("sends a resolution with its reason", async () => {
    const calls = stubApi({
      "/registers/status": () =>
        Response.json(status({ conflicts: 1, ready: false })),
      "/registers/drawings": () =>
        Response.json([
          drawing({ state: "conflict", conflict_reason: "They disagree." }),
        ]),
      "/resolve": () => Response.json(drawing()),
    });
    renderAt(`/bids/${BID}/registers`);

    await userEvent.type(
      await screen.findByLabelText("Reason"),
      "misnamed file",
    );
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/resolve"))).toBe(true),
    );
    const sent = calls.find((call) => call.url.endsWith("/resolve"));
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({
      outcome: "current",
      reason: "misnamed file",
    });
  });

  // req: FR-DOC-02
  it("asks a person to identify a sheet the reader was unsure of", async () => {
    stubApi({
      "/registers/status": () =>
        Response.json(status({ unidentified: 1, ready: false })),
      "/registers/drawings": () =>
        Response.json([
          drawing({ state: "received", sheet_number: null, revision: null }),
        ]),
    });
    renderAt(`/bids/${BID}/registers`);

    expect(
      await screen.findByText(/Say what this sheet is/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Not a drawing" }),
    ).toBeInTheDocument();
  });

  it("flags a sheet for manual takeoff where the input is poor", async () => {
    stubApi({
      "/registers/status": () => Response.json(status()),
      "/registers/drawings": () =>
        Response.json([
          drawing({ quality_band: "low", manual_takeoff_recommended: true }),
        ]),
    });
    renderAt(`/bids/${BID}/registers`);

    expect(
      await screen.findByText("Manual takeoff recommended"),
    ).toBeInTheDocument();
  });

  it("shows the specification register on its tab", async () => {
    stubApi({
      "/registers/status": () => Response.json(status()),
      "/registers/drawings": () => Response.json([]),
      "/registers/documents": () =>
        Response.json([
          {
            revision_id: "d1",
            document_id: "doc1",
            filename: "Particular Specification Rev 2.docx",
            doc_type: "specification",
            type_confidence: 0.9,
            type_decided_by: "rules",
            doc_key: "PARTICULAR SPECIFICATION",
            title: "Particular Specification for Fire Protection Services",
            revision: "2",
            revision_date: null,
            state: "current",
            conflict_reason: null,
            addendum: null,
          },
        ]),
    });
    renderAt(`/bids/${BID}/registers`);

    await userEvent.click(
      await screen.findByRole("tab", { name: "Specification register" }),
    );

    expect(
      await screen.findByText(
        "Particular Specification for Fire Protection Services",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("specification")).toBeInTheDocument();
  });
});

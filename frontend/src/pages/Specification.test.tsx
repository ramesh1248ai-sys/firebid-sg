import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The specification page. What matters: every attribute's clause is one click away, with
 * the words it rests on marked, and a citation that did not hold up says so.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const CLAUSE = "77777777-0000-4000-8000-000000000001";

function attribute(overrides: Record<string, unknown> = {}) {
  return {
    lineage_id: "88888888-0000-4000-8000-000000000001",
    version: 1,
    system: "sprinkler",
    attribute: "joining_method",
    value: "grooved",
    dn_min: 65,
    dn_max: null,
    condition: null,
    state: "proposed",
    method: "rule",
    confidence: 0.9,
    citation_ok: true,
    citation_reason: "the clause states it",
    clause_id: CLAUSE,
    clause_number: "2.1.2",
    quote:
      "Pipes 65 mm and above shall be joined by roll-grooved mechanical couplings.",
    document_id: "99999999-0000-4000-8000-000000000001",
    document_revision_id: "99999999-0000-4000-8000-000000000002",
    document_title: "PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES",
    revision_label: "B",
    model: null,
    prompt_version: null,
    verified_by: null,
    ...overrides,
  };
}

const CLAUSE_TEXT = {
  id: CLAUSE,
  number: "2.1.2",
  heading: "",
  text:
    "Pipes up to and including 50 mm shall be joined by screwed fittings. " +
    "Pipes 65 mm and above shall be joined by roll-grooved mechanical couplings.",
  anchor: { paragraph: 10 },
  system: "sprinkler",
  document_id: "99999999-0000-4000-8000-000000000001",
  document_title: "PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES",
  revision_label: "B",
};

describe("the specification page", () => {
  beforeEach(() =>
    signedInAs({
      name: "Ethan Lim",
      preferred_username: "estimator@firebid.test",
      roles: ["estimator"],
    }),
  );

  // req: FR-SPEC-05
  it("opens the cited clause with the words it rests on marked", async () => {
    stubApi({
      "/spec/attributes": () => Response.json([attribute()]),
      [`/spec/clauses/${CLAUSE}`]: () => Response.json(CLAUSE_TEXT),
    });
    renderAt(`/bids/${BID}/specification`);

    await userEvent.click(await screen.findByRole("button", { name: "2.1.2" }));

    const panel = await screen.findByRole("complementary", {
      name: "Cited clause",
    });
    expect(within(panel).getByText(/paragraph 10/)).toBeInTheDocument();
    expect(panel.querySelector("mark")?.textContent).toBe(
      "Pipes 65 mm and above shall be joined by roll-grooved mechanical couplings.",
    );
    expect(screen.getByText("DN 65 and above")).toBeInTheDocument();
  });

  // req: FR-SPEC-05
  it("shows why a citation did not hold up", async () => {
    stubApi({
      "/spec/attributes": () =>
        Response.json([
          attribute({
            citation_ok: false,
            confidence: 0.2,
            method: "model",
            model: "claude-opus-5",
            citation_reason: "clause 3.1 does not state 'grooved'",
          }),
        ]),
    });
    renderAt(`/bids/${BID}/specification`);

    expect(
      await screen.findByText("clause 3.1 does not state 'grooved'"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/to confirm · model claude-opus-5 · 20%/),
    ).toBeInTheDocument();
  });

  // req: FR-SPEC-01
  it("confirms an attribute", async () => {
    const calls = stubApi({
      "/spec/attributes": () => Response.json([attribute()]),
      "/decide": () =>
        Response.json(
          attribute({ state: "verified", verified_by: "Ethan Lim" }),
        ),
    });
    renderAt(`/bids/${BID}/specification`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Confirm" }),
    );

    const decision = calls.find((call) => call.url.endsWith("/decide"));
    expect(decision?.method).toBe("POST");
    expect(JSON.parse(decision?.body ?? "{}")).toMatchObject({
      verdict: "confirm",
      dn_min: 65,
    });
  });
});

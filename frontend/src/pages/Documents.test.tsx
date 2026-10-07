import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The tender documents page, from an estimator's side.
 *
 * The tests that matter most are the unhappy ones. A page that shows twelve sheets and says
 * nothing about the four files that were refused is how a bid gets priced short.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const SHEET = "7b2e1d4f-0000-4000-8000-000000000002";

function progress(overrides: Record<string, unknown> = {}) {
  return {
    total: 3,
    counts: {
      received: 0,
      awaiting_scan: 0,
      processing: 0,
      done: 3,
      rejected: 0,
      quarantined: 0,
    },
    sheets: 3,
    finished: true,
    failures: [],
    ...overrides,
  };
}

function sheet(overrides: Record<string, unknown> = {}) {
  return {
    id: SHEET,
    document_id: "8c3f2e5a-0000-4000-8000-000000000003",
    filename: "FP-L05-201.pdf",
    index_in_document: 0,
    layout_name: "page 1",
    width_mm: 841,
    height_mm: 594,
    content_class: "vector",
    max_level: 13,
    base_width_px: 4967,
    base_height_px: 3508,
    has_thumbnail: true,
    source_ref: { document_id: "8c3f2e5a-0000-4000-8000-000000000003" },
    ...overrides,
  };
}

/** The progress stream is a nicety; these tests exercise the page without it. */
function noStream() {
  return new Response(null, { status: 204 });
}

describe("the tender documents page", () => {
  beforeEach(() => signedInAs());

  it("shows how many files are ready", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () => Response.json(progress()),
      "/sheets": () => Response.json([sheet()]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(
      await screen.findByText("3 sheets ready to open."),
    ).toBeInTheDocument();
  });

  it("counts the sheets read while a drawing set is still being read", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({
            counts: {
              received: 0,
              awaiting_scan: 0,
              processing: 1,
              done: 0,
              rejected: 0,
              quarantined: 0,
            },
            total: 1,
            sheets: 121,
            sheets_parsed: 84,
            finished: false,
          }),
        ),
      "/sheets": () => Response.json([sheet()]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText("Reading the set: 84 of 121 sheets read…"),
    ).toBeInTheDocument();
  });

  it("names every file that needs attention, with the reason", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({
            total: 3,
            counts: { done: 1, rejected: 1, quarantined: 1 },
            sheets: 1,
            failures: [
              {
                id: "1",
                filename: "PLAN.dwg",
                state: "rejected",
                reason:
                  "DWG conversion is not available yet; send the DXF export.",
              },
              {
                id: "2",
                filename: "brochure.exe",
                state: "quarantined",
                reason: "malware found: Eicar-Test-Signature",
              },
            ],
          }),
        ),
      "/sheets": () => Response.json([sheet()]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText("2 files need attention"),
    ).toBeInTheDocument();
    expect(screen.getByText("PLAN.dwg")).toBeInTheDocument();
    expect(screen.getByText(/send the DXF export/)).toBeInTheDocument();
    expect(screen.getByText("brochure.exe")).toBeInTheDocument();
    expect(screen.getByText(/Eicar-Test-Signature/)).toBeInTheDocument();
  });

  it("says plainly when a file is waiting on the scanner", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({
            total: 1,
            counts: { awaiting_scan: 1 },
            sheets: 0,
            failures: [
              {
                id: "1",
                filename: "FP-L05-201.pdf",
                state: "awaiting_scan",
                reason: null,
              },
            ],
          }),
        ),
      "/sheets": () => Response.json([]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(await screen.findAllByText("Waiting for the scanner")).toHaveLength(
      2,
    );
  });

  it("says when nothing has been sent yet", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({ total: 0, counts: {}, sheets: 0, finished: false }),
        ),
      "/sheets": () => Response.json([]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText("Nothing has been sent for this bid yet."),
    ).toBeInTheDocument();
  });

  it("lists each sheet with its size and what it is made of", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () => Response.json(progress()),
      "/sheets": () =>
        Response.json([
          sheet(),
          sheet({ id: "other", filename: "FP-L05-202.dxf", layout_name: "A1" }),
        ]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(await screen.findByText("FP-L05-201.pdf")).toBeInTheDocument();
    expect(screen.getByText("FP-L05-202.dxf")).toBeInTheDocument();
    expect(
      screen.getByText("page 1 · 841 × 594 mm · vector"),
    ).toBeInTheDocument();
    expect(screen.getByText("A1 · 841 × 594 mm · vector")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /FP-L05-201/ })).toHaveAttribute(
      "href",
      `/bids/${BID}/sheets/${SHEET}`,
    );
  });

  // req: FR-DOC-01
  it("lists a sheet that could not be read and offers to read it again", async () => {
    let asked = false;
    const calls = stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          asked
            ? progress({
                counts: { received: 1, done: 2 },
                finished: false,
                sheets_parsed: 2,
              })
            : progress({
                unread_sheets: [
                  {
                    id: SHEET,
                    filename: "FP-L06-203.pdf",
                    page: 1,
                    reason:
                      "the sheet's linework could not be read: the file needed more memory",
                  },
                ],
                read_again: { documents: 1, sheets: 1, views: 0 },
              }),
        ),
      "/sheets": () => Response.json([sheet()]),
      "/documents/read-again": () => {
        asked = true;
        return Response.json({ documents: 1, sheets: 1, views: 0 });
      },
    });
    renderAt(`/bids/${BID}/documents`);

    // The drawing says "Ready"; the sheet inside it that failed is said all the same.
    expect(
      await screen.findByText("1 sheet could not be read"),
    ).toBeInTheDocument();
    expect(screen.getByText("FP-L06-203.pdf")).toBeInTheDocument();
    expect(screen.getByText(/needed more memory/)).toBeInTheDocument();
    expect(
      screen.getByText("Reads again 1 drawing. Nothing that was read is lost."),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Read again" }));

    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "POST" &&
            call.url.includes("/documents/read-again"),
        ),
      ).toBe(true);
    });
    // The progress is asked for again: the drawing is being read, and the offer is gone.
    expect(
      await screen.findByText("Reading the set: 2 of 3 sheets read…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Read again" }),
    ).not.toBeInTheDocument();
  });

  // req: FR-DOC-01
  it("offers to find views again after the detector changed, and nothing when all is read", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({ read_again: { documents: 0, sheets: 0, views: 148 } }),
        ),
      "/sheets": () => Response.json([sheet()]),
    });
    const { unmount } = renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText(
        "Reads again the views of 148 sheets found by an older version. Nothing that was read is lost.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/could not be read/)).not.toBeInTheDocument();
    unmount();

    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({ read_again: { documents: 0, sheets: 0, views: 0 } }),
        ),
      "/sheets": () => Response.json([sheet()]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText("3 sheets ready to open."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Read again" }),
    ).not.toBeInTheDocument();
  });

  // req: FR-VIS-02
  it("offers to read symbols again after the symbol detector changed", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(
          progress({
            read_again: { documents: 0, sheets: 0, views: 0, symbols: 148 },
          }),
        ),
      "/sheets": () => Response.json([sheet()]),
    });
    renderAt(`/bids/${BID}/documents`);

    expect(
      await screen.findByText(
        "Reads again the symbols of 148 sheets read by an older version. Nothing that was read is lost.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Read again" }),
    ).toBeInTheDocument();
  });

  it("sends the chosen files and asks for the progress again", async () => {
    const calls = stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(progress({ total: 0, counts: {}, sheets: 0 })),
      "/sheets": () => Response.json([]),
      "/documents": () =>
        Response.json({ stored: [], accounted_for: 1 }, { status: 201 }),
    });
    renderAt(`/bids/${BID}/documents`);

    const file = new File(
      [new Uint8Array([37, 80, 68, 70])],
      "FP-L05-201.pdf",
      {
        type: "application/pdf",
      },
    );
    await userEvent.upload(
      await screen.findByLabelText("Files to upload"),
      file,
    );
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.method === "POST" && call.url.includes("/documents"),
        ),
      ).toBe(true);
    });
  });

  it("says why an upload was refused", async () => {
    stubApi({
      "/progress/stream": noStream,
      "/progress": () =>
        Response.json(progress({ total: 0, counts: {}, sheets: 0 })),
      "/sheets": () => Response.json([]),
      "/documents": () =>
        Response.json(
          { detail: "'PLAN.dwg' is larger than 200 MB" },
          { status: 413 },
        ),
    });
    renderAt(`/bids/${BID}/documents`);

    const file = new File([new Uint8Array([1])], "PLAN.dwg");
    await userEvent.upload(
      await screen.findByLabelText("Files to upload"),
      file,
    );
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "larger than 200 MB",
    );
  });
});

describe("the sheet viewer", () => {
  beforeEach(() => signedInAs());

  it("shows the sheet's name, size and class", async () => {
    stubApi({
      [`/sheets/${SHEET}`]: () =>
        Response.json({
          ...sheet(),
          tile_source: {
            width: 4967,
            height: 3508,
            tileSize: 256,
            tileOverlap: 1,
            minLevel: 0,
            maxLevel: 13,
            preRenderedLevels: [0, 1, 2],
            tileUrl: `/bids/${BID}/sheets/${SHEET}/tiles`,
          },
        }),
    });
    renderAt(`/bids/${BID}/sheets/${SHEET}`);

    expect(
      await screen.findByRole("heading", { name: "FP-L05-201.pdf" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("page 1 · 841 × 594 mm · vector"),
    ).toBeInTheDocument();
  });

  // req: FR-DOC-06
  it("says a poor scan needs a manual takeoff, and why", async () => {
    stubApi({
      [`/sheets/${SHEET}`]: () =>
        Response.json({
          ...sheet(),
          tile_source: null,
          content_class: "raster",
          quality_band: "low",
          manual_takeoff_recommended: true,
          quality_detail: {
            quality: {
              expectation: "The source is poor.",
              reasons: ["a raster sheet", "72 dpi, below 200"],
            },
          },
        }),
    });
    renderAt(`/bids/${BID}/sheets/${SHEET}`);

    expect(
      await screen.findByText(
        /Low expected accuracy · Manual takeoff recommended/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/72 dpi, below 200/)).toBeInTheDocument();
  });

  it("says so when the sheet is not finished being read", async () => {
    stubApi({
      [`/sheets/${SHEET}`]: () =>
        Response.json({ ...sheet(), tile_source: null }),
    });
    renderAt(`/bids/${BID}/sheets/${SHEET}`);

    expect(
      await screen.findByText("This sheet has not finished being read yet."),
    ).toBeInTheDocument();
  });

  it("does not confirm a sheet exists to someone not on the bid", async () => {
    stubApi({
      [`/sheets/${SHEET}`]: () =>
        Response.json({ detail: "sheet not found" }, { status: 404 }),
    });
    renderAt(`/bids/${BID}/sheets/${SHEET}`);

    expect(
      await screen.findByRole("heading", { name: "Sheet not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "may not be on this bid's team",
    );
  });
});

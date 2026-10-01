import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * Sending a folder: the person says whose documents each folder holds before anything goes,
 * and every file is accounted for afterwards.
 */

const BID = "6a1f0c3e-0000-4000-8000-000000000001";
const PROGRESS = {
  total: 0,
  counts: { received: 0, awaiting_scan: 0, processing: 0, done: 0, rejected: 0, quarantined: 0 },
  sheets: 0,
  finished: false,
  failures: [],
};

function inFolder(path: string, content = "x"): File {
  const file = new File([content], path.slice(path.lastIndexOf("/") + 1));
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

const TENDER = "MOH/SPK Drawing/L10/A03-10-01.pdf";
const MARKED_UP = "MOH/Rev B/L10/A03-10-01_SJME-B.pdf";
const LOCK = "MOH/~$Response.xlsx";

const PROPOSALS = [
  { path: TENDER, origin: "tender", reason: "no rule says otherwise, so it is taken as client-issued" },
  { path: MARKED_UP, origin: "working", reason: "carries the company's own mark, so it is our working document" },
  { path: LOCK, origin: "ignored", reason: "an office lock or system file, not a document" },
];

function document(overrides: Record<string, unknown>) {
  return {
    id: "8c3f2e5a-0000-4000-8000-000000000003",
    filename: "A03-10-01_SJME-B.pdf",
    media_type: "application/pdf",
    kind: "pdf",
    sha256: "0".repeat(64),
    byte_size: 10,
    state: "done",
    rejected_reason: null,
    source_path: "MOH/tender.zip/Rev B/A03-10-01_SJME-B.pdf",
    origin: "working",
    origin_status: "proposed",
    origin_reason: "carries the company's own mark, so it is our working document",
    ...overrides,
  };
}

describe("sending a folder", () => {
  beforeEach(() => signedInAs());

  // req: FR-DOC-10
  it("proposes each folder's origin, lets a person change it, and sends what they said", async () => {
    const calls = stubApi({
      "/progress/stream": () => new Response(null, { status: 204 }),
      "/progress": () => Response.json(PROGRESS),
      "/sheets": () => Response.json([]),
      "/addenda": () => Response.json([]),
      "/documents/origins": () => Response.json(PROPOSALS),
      "/documents": (call) =>
        call.method === "POST"
          ? Response.json(
              {
                stored: [document({ origin_status: "confirmed" }), document({ id: "x", origin: "tender" })],
                duplicates: [],
                rejected: [],
                quarantined: [],
                awaiting_scan: [],
                ignored: [],
                accounted_for: 2,
              },
              { status: 201 },
            )
          : Response.json([]),
    });
    renderAt(`/bids/${BID}/documents`);

    await userEvent.upload(await screen.findByLabelText("Folder to upload"), [
      inFolder(TENDER),
      inFolder(MARKED_UP),
      inFolder(LOCK),
    ]);

    const table = await screen.findByRole("table", { name: "Folders to send" });
    expect(within(table).getByLabelText("Origin of MOH/SPK Drawing/L10")).toHaveValue("tender");
    expect(within(table).getByLabelText("Origin of MOH/Rev B/L10")).toHaveValue("working");
    expect(within(table).getByLabelText("Origin of MOH")).toHaveValue("ignored");
    expect(table).toHaveTextContent("carries the company's own mark");
    // Two of the three go, and one of those is read.
    expect(screen.getByRole("button", { name: "Send 2 files (1 to be read)" })).toBeEnabled();

    // The person says the marked-up folder is reference instead.
    await userEvent.selectOptions(within(table).getByLabelText("Origin of MOH/Rev B/L10"), "reference");
    await userEvent.click(screen.getByRole("button", { name: "Send 2 files (1 to be read)" }));

    expect(await screen.findByText(/2 stored, 0 already on the bid, 1 left out, 0 not taken/)).toBeVisible();
    const proposed = calls.find((call) => call.url.endsWith("/documents/origins"));
    expect(JSON.parse(proposed!.body!)).toEqual([TENDER, MARKED_UP, LOCK]);
    expect(calls.filter((call) => call.method === "POST" && call.url.endsWith("/documents"))).toHaveLength(1);
  });

  // req: FR-DOC-09
  it("names a file too large to send instead of dropping it", async () => {
    stubApi({
      "/progress/stream": () => new Response(null, { status: 204 }),
      "/progress": () => Response.json(PROGRESS),
      "/sheets": () => Response.json([]),
      "/addenda": () => Response.json([]),
      "/documents/origins": () =>
        Response.json([{ path: "MOH/everything.zip", origin: "tender", reason: "no rule says otherwise" }]),
      "/documents": () => Response.json([]),
    });
    renderAt(`/bids/${BID}/documents`);
    const huge = inFolder("MOH/everything.zip");
    Object.defineProperty(huge, "size", { value: 532 * 1024 * 1024 });

    await userEvent.upload(await screen.findByLabelText("Folder to upload"), [huge]);
    await userEvent.click(await screen.findByRole("button", { name: "Send 1 files (1 to be read)" }));

    const refused = await screen.findByRole("list", { name: "Files not taken" });
    expect(refused).toHaveTextContent("MOH/everything.zip");
    expect(refused).toHaveTextContent("larger than 200 MB");
  });

  // req: FR-DOC-10
  it("lists a document waiting for its origin, and reads it when a person says it is tender", async () => {
    const calls = stubApi({
      "/progress/stream": () => new Response(null, { status: 204 }),
      "/progress": () => Response.json(PROGRESS),
      "/sheets": () => Response.json([]),
      "/addenda": () => Response.json([]),
      "/documents/origin": () => Response.json({ changed: ["8c3f2e5a-0000-4000-8000-000000000003"], refused: {} }),
      "/documents": () => Response.json([document({})]),
    });
    renderAt(`/bids/${BID}/documents`);

    const waiting = await screen.findByRole("list", { name: "Origins to confirm" });
    expect(waiting).toHaveTextContent("MOH/tender.zip/Rev B/A03-10-01_SJME-B.pdf");
    expect(waiting).toHaveTextContent("Our working documents");

    await userEvent.click(within(waiting).getByRole("button", { name: "It is a tender document: read it" }));

    const sent = calls.find((call) => call.url.endsWith("/documents/origin"));
    expect(JSON.parse(sent!.body!)).toEqual({
      document_ids: ["8c3f2e5a-0000-4000-8000-000000000003"],
      origin: "tender",
    });
  });
});

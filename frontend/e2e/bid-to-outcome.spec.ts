import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import {
  type APIRequestContext,
  type Browser,
  expect,
  type Page,
  request,
  test,
} from "@playwright/test";

/**
 * One bid from registered to awarded, through the real stack, by the people whose step each
 * is: the bid manager builds the team and moves the bid on, the Commercial Director decides
 * to bid, the Senior Estimator approves G2, the Commercial Director G3 and G4, and the bid
 * manager records the outcome.
 *
 * The steps that have had a screen and a test since Phase 1 (reading the tender, the
 * workbench, pricing, the risk register) are done here through the API, as a person would
 * through those screens; `workbench.spec.ts` and the page tests cover them. What this test
 * is for is the joins between them, which nothing else exercises: that a bid whose team was
 * built on screen can be taken by that team all the way to a frozen submission.
 */

const PASSWORD = "firebid-dev";
const KEYCLOAK = process.env.E2E_KEYCLOAK_URL ?? "http://localhost:8081";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";

const MANAGER = "bid.manager@firebid.test";
const ESTIMATOR = "estimator@firebid.test";
const SENIOR = "senior.estimator@firebid.test";
const DIRECTOR = "commercial.director@firebid.test";

async function token(username: string): Promise<string> {
  const context = await request.newContext();
  const response = await context.post(
    `${KEYCLOAK}/realms/firebid/protocol/openid-connect/token`,
    {
      form: {
        grant_type: "password",
        client_id: "firebid-dev-tests",
        username,
        password: PASSWORD,
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

async function as(username: string): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: `${BASE}/api/`,
    extraHTTPHeaders: { Authorization: `Bearer ${await token(username)}` },
  });
}

/** A page signed in as one person, in a browser context of their own. */
async function signedIn(browser: Browser, username: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "Your bids" })).toBeVisible();
  return page;
}

/** The basement car park tender, the company's rate and productivity lists, and what each
 * legend row is, from the backend's own fixtures. */
function fixtures(): {
  tender: string[];
  rates: string;
  productivity: string;
  described: Record<string, string>;
} {
  const directory = mkdtempSync(join(tmpdir(), "firebid-outcome-"));
  const script = `
import json, pathlib, sys
sys.path.insert(0, "src")
from firebid.evals import synthetic_boq, synthetic_labour, synthetic_qto, synthetic_rates
from firebid.evals.synthetic import dxf_bytes
from firebid.evals.synthetic_network import DESCRIBED
from firebid.evals.synthetic_spec import CAR_PARK_NOTES, specification_docx, with_risks
out = pathlib.Path(${JSON.stringify(directory)})
plan, _ = synthetic_qto.car_park_plan("FP-B1-201", CAR_PARK_NOTES)
files = {
    "FP-B1-201.dxf": dxf_bytes(plan),
    "Particular Specification Fire Protection.docx": specification_docx(clauses=with_risks()),
    "Bill of Quantities - Fire Sprinkler.xlsx": synthetic_boq.client_boq().payload,
    "rates.xlsx": synthetic_rates.rate_list(),
    "productivity.xlsx": synthetic_labour.productivity_list(),
}
for name, payload in files.items():
    (out / name).write_bytes(payload)
tender = [str(out / name) for name in list(files)[:3]]
print(json.dumps({"tender": tender, "rates": str(out / "rates.xlsx"), "productivity": str(out / "productivity.xlsx"), "described": DESCRIBED}))
`;
  const printed = execFileSync("uv", ["run", "python", "-c", script], {
    cwd: join(process.cwd(), "..", "backend"),
    encoding: "utf-8",
  });
  return JSON.parse(printed.trim().split("\n").pop()!);
}

// req: FR-BID-01
// req: FR-PKG-02
// req: FR-PKG-03
// req: FR-LAB-01
// req: FR-LRN-02
test("a bid goes from registered to awarded, each step by the person whose it is", async ({
  browser,
}) => {
  test.setTimeout(600_000);
  const files = fixtures();
  const eventually = expect.configure({ timeout: 30_000 });

  // A person is known to the platform once they have signed in: each of the team has.
  for (const person of [ESTIMATOR, SENIOR, DIRECTOR]) {
    expect((await (await as(person)).get("bids")).ok()).toBeTruthy();
  }

  // --- The bid manager registers the bid and builds its team -------------------------------
  const manager = await signedIn(browser, MANAGER);
  await manager.getByRole("link", { name: "New bid" }).click();
  await manager.getByLabel("Project").fill(`Outcome E2E ${Date.now()}`);
  // A consultant of its own: a run starts with no legend row named.
  await manager.getByLabel("Design consultant").fill(`E2E Consultants ${Date.now()}`);
  await manager.getByLabel("Client").fill("Main Contractor Pte Ltd");
  await manager.getByLabel("Tender reference").fill(`OUT/E2E/${Date.now()}`);
  await manager.getByLabel("Submission deadline").fill("2027-03-01T17:00");
  await manager.getByLabel("Clarifications close").fill("2027-02-15T17:00");
  await manager.getByRole("button", { name: "Register bid" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText(/^BID-\d{4}-\d+$/);
  const bidId = new URL(manager.url()).pathname.split("/").pop()!;
  const bidPage = `/bids/${bidId}`;

  const team = manager.getByRole("region", { name: "Team" });
  for (const [person, role] of [
    [`Esther Tan (${ESTIMATOR})`, "estimator"],
    [`Samuel Lim (${SENIOR})`, "senior estimator"],
    [`Clara Wong (${DIRECTOR})`, "commercial director"],
  ] as const) {
    await expect(team.getByRole("option", { name: person })).toBeAttached();
    await team.getByLabel("Person to add").selectOption({ label: person });
    // The role they hold in the organisation is the one offered.
    await expect(team.getByLabel("Role on this bid")).toHaveValue(role.replaceAll(" ", "_"));
    await team.getByRole("button", { name: "Add to the team" }).click();
    await expect(
      team.getByRole("list", { name: "People on this bid" }).getByText(person.split(" (")[0]!),
    ).toBeVisible();
  }

  // Someone taken off the bid no longer finds it, and is put back.
  await team.getByRole("button", { name: "Take Esther Tan off the bid" }).click();
  await team.getByRole("button", { name: "Yes, take Esther Tan off" }).click();
  await expect(team.getByLabel("Role of Esther Tan")).toHaveCount(0);
  expect((await (await as(ESTIMATOR)).get(`bids/${bidId}`)).status()).toBe(404);
  await team.getByLabel("Person to add").selectOption({ label: `Esther Tan (${ESTIMATOR})` });
  await team.getByRole("button", { name: "Add to the team" }).click();
  await expect(team.getByLabel("Role of Esther Tan")).toHaveValue("estimator");

  // --- Qualification, and the decision to bid ----------------------------------------------
  const moves = manager.getByRole("region", { name: "Move the bid on" });
  await moves.getByRole("button", { name: "Start qualification" }).click();
  // The decision to bid is the Commercial Director's: shown to the bid manager, not offered.
  await expect(moves.getByRole("button", { name: "Bid (G0)", exact: true })).toBeDisabled();
  await expect(moves).toContainText("For the commercial director.");

  const director = await signedIn(browser, DIRECTOR);
  await director.goto(bidPage);
  await director
    .getByRole("region", { name: "Move the bid on" })
    .getByRole("button", { name: "Bid (G0)", exact: true })
    .click();
  await expect(director.getByText("in preparation").first()).toBeVisible();

  // --- The estimate, as the earlier screens make it ----------------------------------------
  const estimator = await as(ESTIMATOR);
  const senior = await as(SENIOR);
  const base = `bids/${bidId}`;
  for (const file of files.tender) {
    const sent = await estimator.post(`${base}/documents`, {
      multipart: {
        files: {
          name: basename(file),
          mimeType: "application/octet-stream",
          buffer: readFileSync(file),
        },
      },
    });
    expect(sent.status()).toBe(201);
  }
  let rows: { description: string; mapping: { lineage_id: string } | null }[] = [];
  await expect(async () => {
    rows = await (await estimator.get(`${base}/symbols/legend`)).json();
    expect(rows.length).toBeGreaterThanOrEqual(8);
    expect(rows.every((row) => row.mapping)).toBeTruthy();
  }).toPass({ timeout: 240_000, intervals: [3_000] });
  for (const [lineage, description] of new Map(
    rows.map((row) => [row.mapping!.lineage_id, row.description]),
  )) {
    const key = files.described[description]!;
    const confirmed = await estimator.post(`${base}/symbols/mappings/${lineage}/confirm`, {
      data: { object_type: key, attributes: key === "fitting" ? { fitting: "reducer" } : null },
    });
    expect(confirmed.ok()).toBeTruthy();
  }
  let items: { id: string }[] = [];
  const taken = async () => {
    items = await (await senior.get(`${base}/qto/items`)).json();
    expect(items.length).toBeGreaterThanOrEqual(13);
    expect((await (await senior.get(`${base}/qto/g1`)).json()).pending_work).toEqual([]);
  };
  await expect(taken).toPass({ timeout: 240_000, intervals: [3_000] });
  // The grid bubbles and ticks no legend explains are named for what they are: not objects.
  const kinds: { key: string; measure: string }[] = await (
    await senior.get(`${base}/symbols/object-types`)
  ).json();
  const notAnObject = kinds.find((kind) => kind.measure === "none")!.key;
  await expect(async () => {
    const g1 = await (await senior.get(`${base}/qto/g1`)).json();
    for (const symbol of g1.unmapped_symbols as { symbol_key: string }[]) {
      await senior.post(`${base}/symbols/unlisted`, {
        data: { symbol_key: symbol.symbol_key, object_type: notAnObject },
      });
    }
    expect(g1.unmapped_symbols).toEqual([]);
  }).toPass({ timeout: 120_000, intervals: [3_000] });
  await expect(taken).toPass({ timeout: 240_000, intervals: [3_000] });
  expect(
    (await senior.post(`${base}/review/accept`, { data: { item_ids: items.map((i) => i.id) } })).ok(),
  ).toBeTruthy();
  expect((await senior.post(`${base}/qto/g1/approve`, { data: { comment: "checked" } })).status()).toBe(
    201,
  );
  expect((await estimator.post(`${base}/boq/build`, { data: {} })).status()).toBe(201);

  const rates = await senior.post("rates/import", {
    multipart: {
      file: {
        name: "rates.xlsx",
        mimeType: "application/octet-stream",
        buffer: readFileSync(files.rates),
      },
    },
  });
  expect((await rates.json()).imported).toBe(true);

  // The client's bill, once read, is matched to ours line by line and each match confirmed.
  let mappings: { mapping_id: string | null; state: string | null; maps_to: string | null }[] = [];
  await expect(async () => {
    await estimator.post(`${base}/boq/mappings/propose`);
    mappings = await (await estimator.get(`${base}/boq/mappings`)).json();
    expect(mappings.filter((row) => row.state === "proposed" && row.maps_to).length).toBeGreaterThan(
      5,
    );
  }).toPass({ timeout: 120_000, intervals: [3_000] });
  for (const row of mappings) {
    if (row.mapping_id && row.state === "proposed" && row.maps_to) {
      const done = await estimator.post(`${base}/boq/mappings/${row.mapping_id}`, {
        data: { decision: "confirm" },
      });
      expect(done.ok()).toBeTruthy();
    }
  }
  expect((await estimator.post(`${base}/pricing/run`)).ok()).toBeTruthy();

  // --- The Senior Estimator brings in the productivity list, on its own screen --------------
  const seniorPage = await signedIn(browser, SENIOR);
  await seniorPage.getByRole("link", { name: "Rates" }).click();
  const library = seniorPage.getByRole("region", { name: "Productivity library" });
  await library.getByLabel(/Import a productivity list/).setInputFiles(files.productivity);
  await library.getByRole("button", { name: "Import the productivity list" }).click();
  await expect(library.getByRole("status")).toContainText("Imported:");
  await expect(library.getByRole("table", { name: "Productivity" })).toContainText(
    "company standard: PS-2026",
  );
  const labour = await (await senior.get(`${base}/labour`)).json();
  expect(Number(labour.hours)).toBeGreaterThan(0);

  // --- Scope and risk settled, so that G3 has what it waits for ----------------------------
  // The checklist is pre-filled from the scope matrix, which the specification's analysis fills.
  expect((await estimator.post(`${base}/spec/analysis/run`)).ok()).toBeTruthy();
  expect((await senior.post(`${base}/risk/checklist/build`)).ok()).toBeTruthy();
  expect((await senior.post(`${base}/risk/find`)).ok()).toBeTruthy();
  const risk = await (await senior.get(`${base}/risk`)).json();
  for (const check of risk.checklist as { id: string; status: string }[]) {
    if (check.status === "open") {
      const done = await senior.put(`${base}/risk/checklist/${check.id}`, {
        data: { status: "excluded", note: "not in this tender" },
      });
      expect(done.ok()).toBeTruthy();
    }
  }
  for (const one of risk.risks as { id: string; proposed_treatment: string }[]) {
    const done = await senior.put(`${base}/risk/risks/${one.id}`, {
      data: { treatment: one.proposed_treatment, status: "treated" },
    });
    expect(done.ok()).toBeTruthy();
  }

  // --- Submit for review, then the gates, each by its own approver --------------------------
  await manager.goto(bidPage);
  await manager
    .getByRole("region", { name: "Move the bid on" })
    .getByRole("button", { name: "Submit for review" })
    .click();
  await expect(manager.getByText("under review").first()).toBeVisible();

  await seniorPage.goto(`${bidPage}/review`);
  const gates = seniorPage.getByRole("list", { name: "Gates" });
  await expect(gates).toBeVisible();
  // The Senior Estimator is offered G2 and no other gate.
  await expect(seniorPage.getByRole("button", { name: "Approve G3" })).toHaveCount(0);
  await seniorPage.getByLabel("Approval comment").fill("bill reconciled");
  await seniorPage.getByRole("button", { name: "Approve G2" }).click();
  await eventually(gates.getByRole("listitem").nth(1)).toContainText("approved");
  await expect(gates.getByRole("listitem").nth(1)).toContainText("bill reconciled");

  await director.goto(`${bidPage}/review`);
  await expect(director.getByRole("button", { name: "Approve G2" })).toHaveCount(0);
  await director.getByLabel("Approval comment").fill("scope and risks reviewed");
  await director.getByRole("button", { name: "Approve G3" }).click();
  const approveG4 = director.getByRole("button", { name: /Approve G4/ });
  await eventually(approveG4).toBeEnabled();
  await approveG4.click();

  // --- The submission is frozen, with the client's own bill priced among its files ----------
  await eventually(director.getByRole("status", { name: "Snapshot verification" })).toContainText(
    "The snapshot verifies",
  );
  const frozen = director.getByRole("list", { name: "Submission files" });
  for (const name of ["company-boq.xlsx", "review-pack.pdf", "client-boq-priced.xlsx"]) {
    await expect(frozen).toContainText(name);
  }
  // A submitted bid is not moved on from its page: only its outcome is left to record.
  await director.goto(bidPage);
  await expect(director.getByText("submitted").first()).toBeVisible();

  // --- The bid manager records the outcome -------------------------------------------------
  await manager.goto(`${bidPage}/review`);
  const outcome = manager.getByRole("form", { name: "Outcome" });
  await outcome.getByLabel("Result").selectOption("awarded");
  await outcome.getByLabel("Awarded price").fill("6454.39");
  await outcome.getByLabel("Reasons").fill("Lowest compliant offer");
  await outcome.getByRole("button", { name: "Record the outcome" }).click();
  await eventually(outcome).toContainText("recorded as awarded");
  await manager.goto(bidPage);
  await expect(manager.getByText("awarded").first()).toBeVisible();
});

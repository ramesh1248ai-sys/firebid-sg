import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { type APIRequestContext, expect, type Page, request, test } from "@playwright/test";

/**
 * The verification workbench through the real stack (P1-08).
 *
 * A synthetic bid of three sheets (general arrangement, enlarged plan, riser schematic) is
 * uploaded and its legend confirmed through the API, as an estimator would through the
 * symbols page; detection and takeoff run in the worker. Then, in the browser, the Senior
 * Estimator reviews it to G1: every step an estimator takes, and each shows in the audit log.
 *
 * The Senior Estimator does the whole review here: they hold every permission it needs, and
 * signing in one person keeps the test about the workbench rather than bid membership.
 */

const PASSWORD = "firebid-dev";
const SENIOR = "senior.estimator@firebid.test";
const KEYCLOAK = process.env.E2E_KEYCLOAK_URL ?? "http://localhost:8081";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";

test.describe.configure({ mode: "serial" });

// An action taken while detection and takeoff recompute in the background waits for them.
const eventually = expect.configure({ timeout: 30_000 });

let bidId = "";

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

async function asSenior(): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: `${BASE}/api/`,
    extraHTTPHeaders: { Authorization: `Bearer ${await token(SENIOR)}` },
  });
}

/** The three sheets and what each legend row is, from the backend's own fixtures. */
function tender(): { files: string[]; described: Record<string, string> } {
  const directory = mkdtempSync(join(tmpdir(), "firebid-workbench-"));
  const script = `
import json, pathlib, sys
sys.path.insert(0, "src")
from firebid.evals import synthetic, synthetic_qto as q
from firebid.evals.synthetic_network import DESCRIBED
out = pathlib.Path(${JSON.stringify(directory)})
files = []
for number, (document, _) in (("FP-L05-201", q.general_arrangement()), ("FP-L05-301", q.enlarged_plan()), ("FP-SCH-001", q.riser_schematic())):
    files.append(str(synthetic.write_dxf(document, out / f"{number}.dxf")))
print(json.dumps({"files": files, "described": DESCRIBED}))
`;
  const printed = execFileSync("uv", ["run", "python", "-c", script], {
    cwd: join(process.cwd(), "..", "backend"),
    encoding: "utf-8",
  });
  return JSON.parse(printed.trim().split("\n").pop()!);
}

async function signIn(page: Page, username: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "Your bids" })).toBeVisible();
}

test.beforeAll(async () => {
  test.setTimeout(420_000);
  const api = await asSenior();
  const created = await api.post("bids", {
    data: {
      project_name: `Workbench E2E ${Date.now()}`,
      // A consultant of its own: mappings are kept per consultant, and a run must start
      // with nothing named.
      consultant: `E2E Consultants ${Date.now()}`,
      client_name: "Main Contractor Pte Ltd",
      tender_reference: `WB/E2E/${Date.now()}`,
      submission_deadline: new Date(Date.now() + 14 * 86_400_000).toISOString(),
    },
  });
  expect(created.status()).toBe(201);
  bidId = (await created.json()).id;

  const { files, described } = tender();
  for (const file of files) {
    const uploaded = await api.post(`bids/${bidId}/documents`, {
      multipart: {
        files: { name: basename(file), mimeType: "application/dxf", buffer: readFileSync(file) },
      },
    });
    expect(uploaded.status()).toBe(201);
  }

  // The legend, read by the parse job, confirmed row by row as a person would.
  let rows: { description: string; mapping: { lineage_id: string } | null }[] = [];
  await expect(async () => {
    rows = await (await api.get(`bids/${bidId}/symbols/legend`)).json();
    expect(rows.length).toBeGreaterThanOrEqual(8);
    expect(rows.every((row) => row.mapping)).toBeTruthy();
  }).toPass({ timeout: 240_000, intervals: [3_000] });
  const lineages = new Map(rows.map((row) => [row.mapping!.lineage_id, row.description]));
  for (const [lineage, description] of lineages) {
    const key = described[description]!;
    const confirmed = await api.post(`bids/${bidId}/symbols/mappings/${lineage}/confirm`, {
      data: { object_type: key, attributes: key === "fitting" ? { fitting: "reducer" } : null },
    });
    expect(confirmed.ok()).toBeTruthy();
  }

  // Detection and takeoff follow in the worker.
  await expect(async () => {
    const items = await (await api.get(`bids/${bidId}/qto/items`)).json();
    expect(items.length).toBeGreaterThanOrEqual(13);
    const g1 = await (await api.get(`bids/${bidId}/qto/g1`)).json();
    expect(g1.pending_work).toEqual([]);
  }).toPass({ timeout: 240_000, intervals: [3_000] });

  // A count entered with no place on any drawing: its evidence is incomplete, which G1 must
  // refuse until someone deals with it.
  const loose = await api.post(`bids/${bidId}/qto/items`, {
    data: { item_type: "flow_switch", description: "Flow switch (no location)", quantity: "1" },
  });
  expect(loose.status()).toBe(201);
});

async function openWorkbench(page: Page) {
  await signIn(page, SENIOR);
  await page.goto(`/bids/${bidId}/workbench`);
  await expect(page.getByTestId("queue-rows").getByRole("row").first()).toBeVisible();
}

/** A queue line: found with the queue's own search, as a long queue is only partly drawn. */
function row(page: Page, text: string) {
  return page.getByTestId("queue-rows").getByRole("row").filter({ hasText: text });
}

async function find(page: Page, text: string) {
  await page.getByLabel("Find").fill(text);
  return row(page, text);
}

// req: FR-REV-04
test("G1 is disabled while anything blocks it, and says what", async ({ page }) => {
  await openWorkbench(page);
  await page.getByRole("button", { name: "Coverage & G1" }).click();

  const blockers = page.getByRole("list", { name: "What blocks G1" });
  await expect(blockers.getByRole("listitem").filter({ hasText: /Coverage is 0%/ })).toBeVisible();
  await expect(blockers.getByRole("listitem").filter({ hasText: /unresolved duplicate group/ })).toBeVisible();
  await expect(blockers.getByRole("listitem").filter({ hasText: /nobody has named/ })).toBeVisible();
  await expect(blockers.getByRole("listitem").filter({ hasText: /incomplete evidence record/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve G1/ })).toBeDisabled();
});

// req: NFR-10
// req: FR-REV-01
test("a QTO line's evidence is one click away", async ({ page }) => {
  await openWorkbench(page);
  let clicks = 0;
  await (await find(page, "Sprinkler, pendent")).click();
  clicks += 1;

  const panel = page.getByRole("region", { name: /^Item QTO-/ });
  await expect(panel.getByText("FP-L05-201 rev R01")).toBeVisible();
  await panel.getByText("Evidence record").click();
  await expect(panel.getByText(/count of 16 detections on FP-L05-201/)).toBeVisible();
  // The drawing zoomed to it and outlines its marks.
  await expect(page.locator("[data-testid=overlay-highlights] [data-mark-id]").first()).toBeAttached();
  expect(clicks).toBeLessThanOrEqual(2);
});

// req: NFR-12
test("an action shows its result quickly, and undoes", async ({ page }) => {
  await openWorkbench(page);
  const valve = await find(page, "Gate valve, DN150");
  await valve.click();
  await expect(page.getByRole("region", { name: /^Item QTO-/ })).toContainText("proposed");

  // Timed inside the page, from the key press to the line changing: Playwright's own
  // polling would round it up to its retry interval.
  await page.evaluate(() => {
    const record = window as unknown as { __feedbackMs?: number };
    let pressed = 0;
    window.addEventListener("keydown", () => (pressed = performance.now()), {
      capture: true,
      once: true,
    });
    function look() {
      const line = [...document.querySelectorAll("[data-testid=queue-rows] [role=row]")].find(
        (r) => r.textContent?.includes("Gate valve, DN150"),
      );
      if (pressed && line?.textContent?.includes("verified")) {
        record.__feedbackMs = performance.now() - pressed;
      } else {
        requestAnimationFrame(look);
      }
    }
    requestAnimationFrame(look);
  });
  await page.keyboard.press("a");
  const feedbackMs = Math.round(
    (await (
      await page.waitForFunction(
        () => (window as unknown as { __feedbackMs?: number }).__feedbackMs,
      )
    ).jsonValue()) as number,
  );
  await expect(valve).toContainText("verified");
  console.log(`action feedback: ${feedbackMs} ms (accept, key press to the queue saying verified)`);
  test.info().annotations.push({ type: "action feedback ms", description: String(feedbackMs) });
  expect(feedbackMs).toBeLessThan(200);

  await page.keyboard.press("Control+z");
  await expect(valve).toContainText("proposed");
});

// req: FR-REV-01
// req: FR-REV-03
// req: FR-REV-04
// req: FR-QTO-11
test("from the queue to G1, every action in the audit log", async ({ page }) => {
  test.setTimeout(300_000);
  await openWorkbench(page);

  // Name the grid bubbles and ticks no legend explains: not installed objects.
  await page.getByRole("button", { name: "Symbols & scale" }).click();
  const unnamed = page.getByRole("list", { name: "Unnamed symbols" }).getByRole("listitem");
  await eventually(unnamed.first()).toBeVisible({ timeout: 30_000 });
  for (let left = await unnamed.count(); left > 0; left -= 1) {
    const first = unnamed.first();
    await first
      .getByRole("combobox")
      .selectOption({ label: "Not an installed object (annotation)" });
    await first.getByRole("button", { name: "Name" }).click();
    await eventually(unnamed).toHaveCount(left - 1, { timeout: 30_000 });
  }
  await eventually(page.getByText("Every symbol on the Current sheets is named.")).toBeVisible({
    timeout: 60_000,
  });

  // The loose count has nowhere to show: reject it.
  await page.getByRole("button", { name: "Queue" }).click();
  await (await find(page, "Flow switch (no location)")).click();
  const loose = page.getByRole("region", { name: /^Item QTO-/ });
  await loose.getByRole("button", { name: "Reject" }).click();
  await loose.getByRole("combobox", { name: /^Reason/ }).selectOption("not_in_scope");
  await loose.getByRole("button", { name: "Reject", exact: true }).last().click();
  await eventually(row(page, "Flow switch (no location)")).toContainText("rejected");

  // Bulk-accept a page of the queue.
  await page.getByLabel("Find").fill("");
  await page.getByRole("button", { name: /Select page/ }).click();
  await page.getByRole("button", { name: /^Accept \d+$/ }).click();
  await eventually(await find(page, "Sprinkler, pendent")).toContainText("verified");

  // Edit one, with a reason.
  await (await find(page, "Sprinkler, upright")).click();
  const item = page.getByRole("region", { name: /^Item QTO-/ });
  await item.getByRole("button", { name: "Edit" }).click();
  await item.getByLabel("finish", { exact: true }).fill("white");
  await item.getByRole("combobox", { name: /^Reason/ }).selectOption("wrong_attribute");
  await item.getByRole("button", { name: "Save and verify" }).click();
  await eventually(item.getByText(/finish: white · edited by/)).toBeVisible();

  // Reject one, with a reason.
  await (await find(page, "Tee, DN100xDN50")).click();
  await item.getByRole("button", { name: "Reject" }).click();
  await item.getByRole("combobox", { name: /^Reason/ }).selectOption("not_in_scope");
  await item.getByRole("button", { name: "Reject", exact: true }).last().click();
  await eventually(row(page, "Tee, DN100xDN50")).toContainText("rejected");

  // Add what was missed: two flow switches, placed on the drawing.
  await page.getByText("Add what was missed").click();
  const tools = page.getByRole("region", { name: "Manual takeoff" });
  await tools.getByLabel("Type").selectOption({ label: "Flow switch" });
  await tools.getByRole("button", { name: "Start counting" }).click();
  const viewer = page.getByTestId("sheet-viewer");
  const box = (await viewer.boundingBox())!;
  await viewer.click({ position: { x: box.width * 0.5, y: box.height * 0.4 } });
  await viewer.click({ position: { x: box.width * 0.55, y: box.height * 0.4 } });
  await tools.getByRole("button", { name: "Save 2" }).click();
  const manual = page.getByRole("region", { name: /^Item QTO-/ });
  await eventually(manual).toContainText("manual by Samuel");
  await manual.getByRole("button", { name: "Accept" }).click();
  await eventually(manual).toContainText("verified");

  // Resolve the duplicate: the enlarged plan repeats the general arrangement.
  await page.getByRole("button", { name: /^Duplicates/ }).click();
  await page.getByRole("button", { name: /Enlarged plan repeats the general plan/ }).click();
  await page.getByRole("button", { name: "Keep one of each" }).click();
  await eventually(page.getByText(/Decided by Samuel/)).toBeVisible();

  // Coverage reaches 100%, and G1 is passed.
  await page.getByRole("button", { name: "Coverage & G1" }).click();
  await eventually(page.getByRole("meter", { name: "Items verified" })).toHaveAttribute(
    "aria-valuenow",
    "100",
    { timeout: 90_000 },
  );
  const approve = page.getByRole("button", { name: /Approve G1/ });
  await eventually(approve).toBeEnabled({ timeout: 90_000 });
  await approve.click();
  await eventually(page.getByText(/G1 approved at/)).toBeVisible();

  // Every action is in the audit log, with who took it.
  await page.getByRole("link", { name: "History" }).click();
  for (const action of [
    "QTO item: verify",
    "QTO item: edit values",
    "QTO item: reject",
    "manual QTO item: create",
    "duplicate group: decide",
    "symbol mapping:",
    "gate G1: approve",
  ]) {
    await page.getByLabel("Action contains").fill(action);
    await page.getByRole("button", { name: "Apply filters" }).click();
    const found = page.getByRole("row").filter({ hasText: action });
    await eventually(found.first()).toBeVisible();
    await eventually(found.first()).toContainText("Samuel");
  }
});

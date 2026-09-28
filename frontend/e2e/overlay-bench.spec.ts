import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, type Page, request, test } from "@playwright/test";

/**
 * The overlay performance bench (P1-08, NFR-12), run on demand: `OVERLAY_BENCH=1 npx
 * playwright test e2e/overlay-bench.spec.ts`. It is skipped otherwise, because its numbers
 * are about the machine it runs on (the reference workstation), not about correctness.
 *
 * On a real sheet's tiles with 5,000 and 20,000 synthetic marks it measures:
 * - frames per second while the viewer pans and zooms continuously (each frame redraws the
 *   overlay from the spatial index);
 * - the overlay's draw time per frame;
 * - hit-test time for a click.
 *
 * Results are printed and written to `test-results/overlay-bench.json`.
 */

const PASSWORD = "firebid-dev";
const SENIOR = "senior.estimator@firebid.test";
const KEYCLOAK = process.env.E2E_KEYCLOAK_URL ?? "http://localhost:8081";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";

test.skip(!process.env.OVERLAY_BENCH, "the overlay bench runs on demand (OVERLAY_BENCH=1)");
test.describe.configure({ mode: "serial" });

let bidId = "";

test.beforeAll(async () => {
  test.setTimeout(300_000);
  const auth = await request.newContext();
  const token = (
    await (
      await auth.post(`${KEYCLOAK}/realms/firebid/protocol/openid-connect/token`, {
        form: { grant_type: "password", client_id: "firebid-dev-tests", username: SENIOR, password: PASSWORD },
      })
    ).json()
  ).access_token as string;
  const api = await request.newContext({
    baseURL: `${BASE}/api/`,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
  const created = await api.post("bids", {
    data: {
      project_name: `Overlay bench ${Date.now()}`,
      client_name: "Bench",
      tender_reference: `BENCH/${Date.now()}`,
      submission_deadline: new Date(Date.now() + 14 * 86_400_000).toISOString(),
    },
  });
  bidId = (await created.json()).id;
  const directory = mkdtempSync(join(tmpdir(), "firebid-bench-"));
  const script = `
import pathlib, sys
sys.path.insert(0, "src")
from firebid.evals import synthetic, synthetic_qto as q
print(synthetic.write_dxf(q.general_arrangement()[0], pathlib.Path(${JSON.stringify(directory)}) / "FP-L05-201.dxf"))
`;
  const file = execFileSync("uv", ["run", "python", "-c", script], {
    cwd: join(process.cwd(), "..", "backend"),
    encoding: "utf-8",
  })
    .trim()
    .split("\n")
    .pop()!;
  await api.post(`bids/${bidId}/documents`, {
    multipart: { files: { name: "FP-L05-201.dxf", mimeType: "application/dxf", buffer: readFileSync(file) } },
  });
  await expect(async () => {
    const sheets = await (await api.get(`bids/${bidId}/qto/sheets`)).json();
    expect(sheets.length).toBe(1);
  }).toPass({ timeout: 240_000, intervals: [3_000] });
});

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("#username").fill(SENIOR);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "Your bids" })).toBeVisible();
}

/** What the bench page leaves on `window` (typed loosely: the test runs outside the app). */
interface Point {
  x: number;
  y: number;
}
interface BenchWindow {
  __workbenchViewer?: {
    world: { getItemCount(): number };
    viewport: {
      zoomTo(zoom: number, point?: Point, immediately?: boolean): void;
      zoomBy(factor: number, point?: Point, immediately?: boolean): void;
      panBy(delta: Point, immediately?: boolean): void;
      getCenter(): Point;
    };
  };
  __workbenchFrames?: { frames: number; totalDrawMs: number; maxDrawMs: number };
  __overlayBench?: { hitTest(samples: number): { meanMs: number; maxMs: number } };
}

interface Result {
  marks: number;
  fps: number;
  frames: number;
  meanDrawMs: number;
  maxDrawMs: number;
  hitMeanMs: number;
  hitMaxMs: number;
}

async function measure(page: Page, n: number): Promise<Result> {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`/bids/${bidId}/workbench/bench?n=${n}`);
  await expect(page.getByTestId("bench-status")).toContainText(`${n} marks`);
  await page.waitForFunction(() =>
    Boolean((window as unknown as BenchWindow).__workbenchViewer?.world.getItemCount()),
  );
  await page.waitForTimeout(2_000);
  return page.evaluate(async (count) => {
    const bench = window as unknown as BenchWindow;
    const viewer = bench.__workbenchViewer!;
    // Zoom in to where marks are dense and small, then pan and zoom for three seconds.
    viewer.viewport.zoomTo(4, undefined, true);
    await new Promise((resolve) => setTimeout(resolve, 500));
    const before = { ...bench.__workbenchFrames! };
    const start = performance.now();
    let frames = 0;
    await new Promise<void>((resolve) => {
      function step() {
        frames += 1;
        const t = performance.now() - start;
        // OpenSeadragon's own Point, taken from one it made.
        const PointOf = viewer.viewport.getCenter().constructor as new (
          x: number,
          y: number,
        ) => Point;
        viewer.viewport.panBy(new PointOf(0.002 * Math.cos(t / 400), 0.0015), true);
        viewer.viewport.zoomBy(1 + 0.01 * Math.sin(t / 300), undefined, true);
        if (t < 3_000) requestAnimationFrame(step);
        else resolve();
      }
      requestAnimationFrame(step);
    });
    const seconds = (performance.now() - start) / 1000;
    const after = bench.__workbenchFrames!;
    const draws = after.frames - before.frames;
    const hits = bench.__overlayBench!.hitTest(2_000);
    return {
      marks: count,
      fps: frames / seconds,
      frames: draws,
      meanDrawMs: (after.totalDrawMs - before.totalDrawMs) / Math.max(draws, 1),
      maxDrawMs: after.maxDrawMs,
      hitMeanMs: hits.meanMs,
      hitMaxMs: hits.maxMs,
    };
  }, n);
}

test("overlay performance with 5,000 and 20,000 marks", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  // A first pass loads and caches the tiles; then a baseline with no overlay, so the
  // overlay's own cost shows apart from the tiles'. Each case is run twice.
  await measure(page, 5_000);
  const results = [];
  for (const n of [0, 5_000, 20_000]) {
    for (let pass = 0; pass < 2; pass += 1) results.push(await measure(page, n));
  }
  mkdirSync("test-results", { recursive: true });
  writeFileSync("test-results/overlay-bench.json", JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  expect(results[0]!.fps).toBeGreaterThan(0);
});

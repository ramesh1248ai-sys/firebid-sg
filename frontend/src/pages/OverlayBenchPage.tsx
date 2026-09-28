import { useEffect, useMemo } from "react";
import { useParams, useSearchParams } from "react-router";

import { DrawingViewer } from "@/workbench/DrawingViewer";
import { useTileSource, useWorkbenchSheets } from "@/workbench/data";
import { type Mark, MarkIndex, NO_LAYERS_HIDDEN } from "@/workbench/marks";

/**
 * The overlay performance bench (P1-08, NFR-12): a real sheet's tiles with `n` synthetic
 * marks over them, spread like a dense floor plate (heads on a grid, runs along the rows).
 *
 * Not linked from anywhere. `scripts/overlay-bench` (Playwright) opens it, pans and zooms
 * the viewer, and reads the frame counters the viewer keeps on `window`, and the hit-test
 * timings this page measures when asked.
 */

declare global {
  interface Window {
    __overlayBench?: {
      marks: number;
      hitTest: (samples: number) => { meanMs: number; maxMs: number };
    };
  }
}

function syntheticMarks(n: number, width: number, height: number): Mark[] {
  const marks: Mark[] = [];
  const runs = Math.round(n * 0.2);
  const heads = n - runs;
  const columns = Math.ceil(Math.sqrt((heads * width) / height));
  const rows = Math.ceil(heads / columns);
  const dx = (width - 40) / columns;
  const dy = (height - 40) / rows;
  const statuses = ["proposed", "verified", "edited", "rejected", "manual"];
  const bands = ["high", "medium", "low"];
  for (let i = 0; i < heads; i++) {
    const x = 20 + (i % columns) * dx + dx / 2;
    const y = 20 + Math.floor(i / columns) * dy + dy / 2;
    marks.push({
      id: `h${i}`,
      kind: "detection",
      object_type: "sprinkler_pendent",
      box: [x - 0.8, y - 0.8, x + 0.8, y + 0.8],
      status: statuses[i % statuses.length]!,
      band: bands[i % bands.length]!,
      confidence: 0.5 + (i % 50) / 100,
      item_id: `item${i % 40}`,
      item_human_id: `QTO-${String(i % 40).padStart(6, "0")}`,
      x,
      y,
      points: [],
      label: null,
    });
  }
  for (let i = 0; i < runs; i++) {
    const y = 20 + (i % rows) * dy + dy / 2;
    const x0 = 20 + Math.floor(i / rows) * dx * 3;
    const points = [
      [x0, y],
      [x0 + dx * 3, y],
    ];
    marks.push({
      id: `r${i}`,
      kind: "run",
      object_type: "pipe_branch",
      box: [x0, y, x0 + dx * 3, y],
      status: "proposed",
      band: bands[i % bands.length]!,
      confidence: 0.8,
      item_id: "run-item",
      item_human_id: "QTO-000999",
      x: null,
      y: null,
      points,
      label: "DN50",
    });
  }
  return marks;
}

export function OverlayBenchPage() {
  const { bidId = "" } = useParams();
  const [search] = useSearchParams();
  const n = Number(search.get("n") ?? 5000);
  const sheets = useWorkbenchSheets(bidId);
  const sheet = sheets.data?.find((s) => s.sheet_id === search.get("sheet")) ?? sheets.data?.[0];
  const tiles = useTileSource(bidId, sheet?.sheet_id ?? null);
  const width = sheet?.width_mm ?? 1189;
  const height = sheet?.height_mm ?? 841;
  const index = useMemo(
    () => new MarkIndex(syntheticMarks(n, width, height)),
    [n, width, height],
  );
  useEffect(() => {
    const built = index;
    window.__overlayBench = {
      marks: built.marks.length,
      hitTest(samples: number) {
        let total = 0;
        let max = 0;
        for (let i = 0; i < samples; i++) {
          const x = Math.random() * width;
          const y = Math.random() * height;
          const started = performance.now();
          built.at(x, y, 2);
          const spent = performance.now() - started;
          total += spent;
          max = Math.max(max, spent);
        }
        return { meanMs: total / samples, maxMs: max };
      },
    };
  }, [index, width, height]);
  const selected = useMemo(() => new Set<string>(), []);

  return (
    <section className="h-screen p-2">
      <p className="text-sm" data-testid="bench-status">
        {index.marks.length} marks on {sheet?.sheet_number ?? "…"}
      </p>
      {tiles.data && sheet?.width_mm ? (
        <DrawingViewer
          source={tiles.data}
          widthMm={sheet.width_mm}
          index={index}
          layers={NO_LAYERS_HIDDEN}
          selected={selected}
          className="h-[calc(100vh-3rem)] w-full"
          exposeViewer
        />
      ) : (
        <p role="status">Opening the sheet…</p>
      )}
    </section>
  );
}

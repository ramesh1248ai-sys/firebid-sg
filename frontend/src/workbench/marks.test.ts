import { describe, expect, it } from "vitest";

import {
  type Mark,
  MarkIndex,
  NO_LAYERS_HIDDEN,
  pickInto,
  toScreen,
  toSheet,
  viewBox,
} from "./marks";
import { drawMarks } from "./render";

/**
 * The overlay's geometry: what is in view, what is under the pointer, what a lasso catches,
 * and that a frame draws only what the viewport can see (FR-REV-01, ADR-005).
 */

function symbol(id: string, x: number, y: number, extra: Partial<Mark> = {}): Mark {
  return {
    id,
    kind: "detection",
    object_type: "sprinkler_pendent",
    box: [x - 1.5, y - 1.5, x + 1.5, y + 1.5],
    status: "proposed",
    band: "high",
    confidence: 0.95,
    item_id: "item-1",
    item_human_id: "QTO-000001",
    x,
    y,
    points: [],
    label: null,
    ...extra,
  };
}

function run(id: string, points: number[][]): Mark {
  const xs = points.map((p) => p[0]!);
  const ys = points.map((p) => p[1]!);
  return {
    ...symbol(id, 0, 0),
    kind: "run",
    object_type: "pipe_branch",
    box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
    x: null,
    y: null,
    points,
  };
}

// req: FR-REV-01
describe("the mark index", () => {
  const index = new MarkIndex([
    symbol("a", 10, 10),
    symbol("b", 50, 10),
    symbol("c", 10, 50, { status: "verified", band: "low" }),
    run("pipe", [
      [0, 30],
      [100, 30],
    ]),
  ]);

  it("finds what is in a rectangle", () => {
    expect(index.within([0, 0, 20, 20]).map((m) => m.id)).toEqual(["a"]);
  });

  it("picks the symbol under the pointer, and a pipe only near its line", () => {
    expect(index.at(10.5, 10.2, 1)?.id).toBe("a");
    expect(index.at(60, 30.4, 1)?.id).toBe("pipe");
    // Inside the pipe's box but far from its line: nothing.
    expect(index.at(60, 20, 1)).toBeNull();
  });

  it("ignores what a hidden layer holds", () => {
    const layers = { ...NO_LAYERS_HIDDEN, statuses: new Set(["verified"]) };
    expect(index.at(10, 50, 1, layers)).toBeNull();
  });

  it("lassoes what lies inside a freehand outline", () => {
    const outline = [
      [0, 0],
      [60, 0],
      [60, 20],
      [0, 20],
    ];
    expect(
      index
        .lasso(outline)
        .map((m) => m.id)
        .sort(),
    ).toEqual(["a", "b"]);
  });

  it("an empty sheet has nothing to find", () => {
    const empty = new MarkIndex([]);
    expect(empty.within([0, 0, 1000, 1000])).toEqual([]);
    expect(empty.at(1, 1, 5)).toBeNull();
  });
});

describe("sheet millimetres and screen pixels", () => {
  const t = { scale: 2, offsetX: 100, offsetY: 50 };

  it("round-trip", () => {
    const [px, py] = toScreen(t, 10, 20);
    expect([px, py]).toEqual([120, 90]);
    expect(toSheet(t, px, py)).toEqual([10, 20]);
  });

  it("the view box is the sheet the screen shows", () => {
    expect(viewBox(t, 200, 100)).toEqual([-50, -25, 50, 25]);
  });
});

describe("drawing a frame", () => {
  function fakeContext() {
    const calls: string[] = [];
    const context = new Proxy(
      {},
      {
        get: (_target, name) =>
          typeof name === "string" && !["strokeStyle", "fillStyle", "lineWidth", "globalAlpha"].includes(name)
            ? (..._args: unknown[]) => calls.push(name)
            : undefined,
        set: () => true,
      },
    ) as unknown as CanvasRenderingContext2D;
    return { context, calls };
  }

  it("draws only what is in view, and leaves the selection to the SVG layer", () => {
    const many = Array.from({ length: 1000 }, (_, i) => symbol(`s${i}`, (i % 100) * 10, Math.floor(i / 100) * 10));
    const index = new MarkIndex(many);
    const { context } = fakeContext();
    // One pixel per millimetre, 100 x 50 px: the top-left 100 x 50 mm of a 1000 x 100 grid.
    const drawn = drawMarks(
      context,
      index,
      { scale: 1, offsetX: 0, offsetY: 0 },
      { width: 100, height: 50 },
      NO_LAYERS_HIDDEN,
      new Set(["s0"]),
    );
    // Symbols at x 0..100 and y 0..50 (11 x 6, boxes overlapping the edges), less s0.
    expect(drawn).toBe(11 * 6 - 1);
  });
});

describe("picking", () => {
  it("replaces the selection, or with Shift toggles one mark in it", () => {
    expect([...pickInto(new Set(["a"]), "b", false)]).toEqual(["b"]);
    expect([...pickInto(new Set(["a"]), "b", true)].sort()).toEqual(["a", "b"]);
    expect([...pickInto(new Set(["a", "b"]), "a", true)]).toEqual(["b"]);
  });
});

import Flatbush from "flatbush";

/**
 * What the workbench draws over a sheet, and the geometry that makes it fast (ADR-005).
 *
 * Every mark is in sheet millimetres, as the API gives it. A Flatbush index over their boxes
 * answers "what is in this rectangle" in well under a millisecond for tens of thousands of
 * marks, which is what keeps redraws, clicks and lasso selections quick: each frame draws
 * only what the viewport can see, and a click looks at only what is under it.
 */

export type Status =
  | "proposed"
  | "verified"
  | "edited"
  | "rejected"
  | "manual"
  | "detected"
  | "superseded"
  | "baselined"
  | "duplicate"
  | "not_taken_off";

export type Band = "high" | "medium" | "low";

export interface Mark {
  id: string;
  kind: "detection" | "run" | "manual" | string;
  object_type: string;
  box: number[]; // x0, y0, x1, y1 in sheet mm
  status: Status | string;
  band: Band | string;
  confidence: number | null;
  item_id: string | null;
  item_human_id: string | null;
  x: number | null;
  y: number | null;
  points: number[][];
  label: string | null;
}

export type Box = [number, number, number, number];

/** Status colours: one hue each, readable on a white drawing and in dark mode. */
export const STATUS_COLOURS: Record<string, string> = {
  proposed: "#2563eb",
  detected: "#2563eb",
  verified: "#16a34a",
  edited: "#9333ea",
  rejected: "#dc2626",
  manual: "#ea580c",
  baselined: "#15803d",
  superseded: "#9ca3af",
  duplicate: "#9ca3af",
  not_taken_off: "#9ca3af",
};

/** Confidence as line weight and fill: the less sure, the louder. */
export const BAND_STYLE: Record<string, { width: number; fill: number }> = {
  high: { width: 1, fill: 0.08 },
  medium: { width: 1.5, fill: 0.18 },
  low: { width: 2.5, fill: 0.3 },
};

export interface Layers {
  types: Set<string>; // hidden object types
  statuses: Set<string>; // hidden statuses
  bands: Set<string>; // hidden confidence bands
}

export const NO_LAYERS_HIDDEN: Layers = {
  types: new Set(),
  statuses: new Set(),
  bands: new Set(),
};

export function visible(mark: Mark, layers: Layers): boolean {
  return (
    !layers.types.has(mark.object_type) &&
    !layers.statuses.has(mark.status) &&
    !layers.bands.has(mark.band)
  );
}

/** The marks, indexed by their boxes. Built once per sheet load. */
export class MarkIndex {
  readonly marks: Mark[];
  private readonly index: Flatbush | null;

  constructor(marks: Mark[]) {
    this.marks = marks;
    if (marks.length === 0) {
      this.index = null;
      return;
    }
    const index = new Flatbush(marks.length);
    for (const mark of marks) {
      index.add(mark.box[0]!, mark.box[1]!, mark.box[2]!, mark.box[3]!);
    }
    index.finish();
    this.index = index;
  }

  /** Marks whose boxes meet a rectangle: the viewport, or a lasso's bounds. */
  within(box: Box): Mark[] {
    if (!this.index) return [];
    return this.index.search(box[0], box[1], box[2], box[3]).map((i) => this.marks[i]!);
  }

  /** The mark under a point, `tolerance` mm around it; the smallest wins where they overlap. */
  at(x: number, y: number, tolerance: number, layers = NO_LAYERS_HIDDEN): Mark | null {
    const near = this.within([x - tolerance, y - tolerance, x + tolerance, y + tolerance])
      .filter((mark) => visible(mark, layers))
      .filter((mark) => mark.kind !== "run" || nearPolyline(mark.points, x, y, tolerance));
    if (near.length === 0) return null;
    return near.reduce((best, mark) => (area(mark.box) < area(best.box) ? mark : best));
  }

  /** Marks inside a freehand lasso: their centres inside the polygon. */
  lasso(polygon: number[][], layers = NO_LAYERS_HIDDEN): Mark[] {
    if (polygon.length < 3) return [];
    return this.within(boundsOf(polygon))
      .filter((mark) => visible(mark, layers))
      .filter((mark) => {
        const [cx, cy] = centre(mark);
        return insidePolygon(polygon, cx, cy);
      });
  }
}

export function area(box: number[]): number {
  return Math.max(box[2]! - box[0]!, 0.01) * Math.max(box[3]! - box[1]!, 0.01);
}

export function centre(mark: Mark): [number, number] {
  if (mark.x !== null && mark.y !== null) return [mark.x, mark.y];
  return [(mark.box[0]! + mark.box[2]!) / 2, (mark.box[1]! + mark.box[3]!) / 2];
}

export function boundsOf(points: number[][]): Box {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [x, y] of points) {
    x0 = Math.min(x0, x!);
    y0 = Math.min(y0, y!);
    x1 = Math.max(x1, x!);
    y1 = Math.max(y1, y!);
  }
  return [x0, y0, x1, y1];
}

/** Even-odd rule. */
export function insidePolygon(polygon: number[][], x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]!;
    const [xj, yj] = polygon[j]!;
    if (yi! > y !== yj! > y && x < ((xj! - xi!) * (y - yi!)) / (yj! - yi!) + xi!) {
      inside = !inside;
    }
  }
  return inside;
}

function nearPolyline(points: number[][], x: number, y: number, tolerance: number): boolean {
  for (let i = 1; i < points.length; i++) {
    if (distanceToSegment(points[i - 1]!, points[i]!, x, y) <= tolerance) return true;
  }
  return false;
}

export function distanceToSegment(a: number[], b: number[], x: number, y: number): number {
  const [ax, ay] = a as [number, number];
  const [bx, by] = b as [number, number];
  const dx = bx - ax;
  const dy = by - ay;
  const length = dx * dx + dy * dy;
  const t = length === 0 ? 0 : Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / length));
  return Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
}

/**
 * Sheet millimetres to screen pixels. OpenSeadragon's viewport runs 0..1 across the image's
 * width (and the same unit down it), and the tile image is the sheet at a fixed dpi, so a
 * scale and an offset are all the transform there is.
 */
export interface Transform {
  scale: number; // screen px per sheet mm
  offsetX: number;
  offsetY: number;
}

export function toScreen(t: Transform, x: number, y: number): [number, number] {
  return [t.offsetX + x * t.scale, t.offsetY + y * t.scale];
}

export function toSheet(t: Transform, px: number, py: number): [number, number] {
  return [(px - t.offsetX) / t.scale, (py - t.offsetY) / t.scale];
}

/** The sheet rectangle the screen shows, for culling. */
export function viewBox(t: Transform, width: number, height: number): Box {
  const [x0, y0] = toSheet(t, 0, 0);
  const [x1, y1] = toSheet(t, width, height);
  return [x0, y0, x1, y1];
}

/** Click a mark: select it alone, or with Shift add it to (or take it from) the selection. */
export function pickInto(current: Set<string>, id: string, additive: boolean): Set<string> {
  if (!additive) return new Set([id]);
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

import type OpenSeadragon from "openseadragon";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { apiUrl } from "@/api/client";
import { accessToken } from "@/auth/oidc";

import {
  type Box,
  type Layers,
  type Mark,
  type MarkIndex,
  STATUS_COLOURS,
  type Transform,
  toScreen,
  toSheet,
} from "./marks";
import { drawMarks } from "./render";

/**
 * The drawing with its proposals over it (FR-REV-01, ADR-005).
 *
 * Three layers, one on top of the other:
 * 1. OpenSeadragon's deep-zoom tiles of the sheet;
 * 2. a canvas with every mark in view, redrawn each animation frame from the spatial index;
 * 3. an SVG with only the selected and hovered marks, the lasso and a measurement in
 *    progress: crisp at any zoom, and each shape carries a title for assistive technology.
 *
 * Tools:
 * - **select:** click a mark to open its item (Shift or Ctrl adds to the selection);
 *   Shift-drag draws a freehand lasso.
 * - **count:** each click places one item at that point.
 * - **length:** each click adds a point; double-click or Enter finishes, Escape abandons.
 */

export interface TileSource {
  width: number;
  height: number;
  tileSize: number;
  tileOverlap: number;
  minLevel: number;
  maxLevel: number;
  tileUrl: string;
}

export type Tool = "select" | "count" | "length";

export interface DrawingViewerProps {
  source: TileSource;
  widthMm: number;
  index: MarkIndex;
  layers: Layers;
  selected: ReadonlySet<string>;
  focus?: { box: number[]; key: number } | null;
  tool?: Tool;
  onPick?: (mark: Mark | null, additive: boolean) => void;
  onLasso?: (marks: Mark[]) => void;
  onCount?: (point: [number, number]) => void;
  onLength?: (points: number[][]) => void;
  onViewChange?: (view: Box) => void;
  className?: string;
}

/** Stats the performance bench reads (P1-08, NFR-12). */
export interface FrameStats {
  frames: number;
  lastDrawMs: number;
  drawn: number;
}

declare global {
  interface Window {
    __workbenchFrames?: FrameStats;
  }
}

const HIT_PX = 6;

export function DrawingViewer({
  source,
  widthMm,
  index,
  layers,
  selected,
  focus,
  tool = "select",
  onPick,
  onLasso,
  onCount,
  onLength,
  onViewChange,
  className,
}: DrawingViewerProps) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewer = useRef<OpenSeadragon.Viewer | null>(null);
  const library = useRef<typeof OpenSeadragon | null>(null);
  const frame = useRef<number | null>(null);
  const [transform, setTransform] = useState<Transform | null>(null);
  const [hovered, setHovered] = useState<Mark | null>(null);
  const [lasso, setLasso] = useState<number[][] | null>(null);
  const [path, setPath] = useState<number[][]>([]);
  const [failed, setFailed] = useState(false);

  // The latest props, for handlers registered once with OpenSeadragon.
  const latest = useRef({ index, layers, selected, tool, onPick, onLasso, onCount, onLength });
  useLayoutEffect(() => {
    latest.current = { index, layers, selected, tool, onPick, onLasso, onCount, onLength };
  });
  const hoveredId = useRef<string | null>(null);

  const currentTransform = useCallback((): Transform | null => {
    const opened = viewer.current;
    const OSD = library.current;
    if (!opened || !OSD || !opened.world.getItemCount()) return null;
    const origin = opened.viewport.pixelFromPoint(new OSD.Point(0, 0), true);
    const across = opened.viewport.pixelFromPoint(new OSD.Point(1, 0), true);
    return { scale: (across.x - origin.x) / widthMm, offsetX: origin.x, offsetY: origin.y };
  }, [widthMm]);

  const redraw = useCallback(() => {
    frame.current = null;
    const element = canvas.current;
    const holder = container.current;
    const t = currentTransform();
    if (!element || !holder || !t) return;
    const width = holder.clientWidth;
    const height = holder.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    if (element.width !== Math.round(width * ratio)) {
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
      element.style.width = `${width}px`;
      element.style.height = `${height}px`;
    }
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const started = performance.now();
    const skip = new Set(latest.current.selected);
    if (hoveredId.current) skip.add(hoveredId.current);
    const drawn = drawMarks(
      context,
      latest.current.index,
      t,
      { width, height },
      latest.current.layers,
      skip,
    );
    const stats = window.__workbenchFrames ?? { frames: 0, lastDrawMs: 0, drawn: 0 };
    window.__workbenchFrames = {
      frames: stats.frames + 1,
      lastDrawMs: performance.now() - started,
      drawn,
    };
    setTransform(t);
  }, [currentTransform]);

  const schedule = useCallback(() => {
    if (frame.current === null) frame.current = requestAnimationFrame(redraw);
  }, [redraw]);

  // Open the viewer once per sheet.
  useEffect(() => {
    let cancelled = false;
    async function open() {
      const [{ default: OSD }, token] = await Promise.all([
        import("openseadragon"),
        accessToken(),
      ]);
      if (cancelled || !container.current) return;
      library.current = OSD;
      const opened = OSD({
        element: container.current,
        prefixUrl: "https://cdn.jsdelivr.net/npm/openseadragon@5/build/openseadragon/images/",
        showNavigator: true,
        navigatorPosition: "BOTTOM_RIGHT",
        loadTilesWithAjax: true,
        ajaxHeaders: token ? { Authorization: `Bearer ${token}` } : {},
        timeout: 60000,
        gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: false },
        tileSources: {
          width: source.width,
          height: source.height,
          tileSize: source.tileSize,
          tileOverlap: source.tileOverlap,
          minLevel: source.minLevel,
          maxLevel: source.maxLevel,
          getTileUrl: (level: number, x: number, y: number) =>
            apiUrl(`${source.tileUrl}/${level}/${x}_${y}.webp`),
        },
      });
      viewer.current = opened;
      opened.addHandler("open-failed", () => setFailed(true));
      for (const name of ["open", "animation", "update-viewport", "resize"] as const) {
        opened.addHandler(name, schedule);
      }
      opened.addHandler("canvas-click", (event) => {
        const t = currentTransform();
        if (!t || !event.quick) return;
        const [x, y] = toSheet(t, event.position.x, event.position.y);
        const now = latest.current;
        const original = event.originalEvent as MouseEvent;
        if (now.tool === "count") {
          now.onCount?.([x, y]);
        } else if (now.tool === "length") {
          setPath((points) => [...points, [x, y]]);
        } else {
          const mark = now.index.at(x, y, HIT_PX / t.scale, now.layers);
          now.onPick?.(mark, original.shiftKey || original.ctrlKey || original.metaKey);
        }
        event.preventDefaultAction = true;
      });
      opened.addHandler("canvas-double-click", (event) => {
        if (latest.current.tool !== "length") return;
        event.preventDefaultAction = true;
        setPath((points) => {
          if (points.length >= 2) latest.current.onLength?.(points);
          return [];
        });
      });
      opened.addHandler("canvas-press", (event) => {
        const original = event.originalEvent as MouseEvent;
        if (latest.current.tool !== "select" || !original.shiftKey) return;
        const t = currentTransform();
        if (!t) return;
        opened.setMouseNavEnabled(false);
        setLasso([toSheet(t, event.position.x, event.position.y)]);
      });
      opened.addHandler("canvas-drag", (event) => {
        setLasso((points) => {
          const t = currentTransform();
          if (!points || !t) return points;
          event.preventDefaultAction = true;
          return [...points, toSheet(t, event.position.x, event.position.y)];
        });
      });
      opened.addHandler("canvas-release", () => {
        setLasso((points) => {
          if (points) {
            opened.setMouseNavEnabled(true);
            if (points.length >= 3) {
              latest.current.onLasso?.(latest.current.index.lasso(points, latest.current.layers));
            }
          }
          return null;
        });
      });
      new OSD.MouseTracker({
        element: opened.canvas,
        moveHandler: (event) => {
          const t = currentTransform();
          if (!t) return;
          const { position } = event as unknown as { position: OpenSeadragon.Point };
          const [x, y] = toSheet(t, position.x, position.y);
          const mark = latest.current.index.at(x, y, HIT_PX / t.scale, latest.current.layers);
          if ((mark?.id ?? null) !== hoveredId.current) {
            hoveredId.current = mark?.id ?? null;
            setHovered(mark);
            schedule();
          }
        },
      });
    }
    open().catch(() => setFailed(true));
    return () => {
      cancelled = true;
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      viewer.current?.destroy();
      viewer.current = null;
    };
  }, [source, schedule, currentTransform]);

  // Anything that changes what is drawn redraws.
  useEffect(schedule, [index, layers, selected, schedule]);

  // Tell the page what the viewport shows (for "select all of this type in view").
  useEffect(() => {
    const holder = container.current;
    if (!transform || !holder || !onViewChange) return;
    const [x0, y0] = toSheet(transform, 0, 0);
    const [x1, y1] = toSheet(transform, holder.clientWidth, holder.clientHeight);
    onViewChange([x0, y0, x1, y1]);
  }, [transform, onViewChange]);

  // Zoom to an item's evidence.
  useEffect(() => {
    const opened = viewer.current;
    const OSD = library.current;
    if (!focus || !opened || !OSD) return;
    const [x0, y0, x1, y1] = focus.box as [number, number, number, number];
    const pad = Math.max(x1 - x0, y1 - y0, 20) * 0.25;
    opened.viewport.fitBoundsWithConstraints(
      new OSD.Rect(
        (x0 - pad) / widthMm,
        (y0 - pad) / widthMm,
        (x1 - x0 + 2 * pad) / widthMm,
        (y1 - y0 + 2 * pad) / widthMm,
      ),
    );
  }, [focus, widthMm]);

  // Keyboard for the length tool.
  useEffect(() => {
    if (tool !== "length") return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setPath([]);
      if (event.key === "Enter") {
        setPath((points) => {
          if (points.length >= 2) latest.current.onLength?.(points);
          return [];
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tool]);

  if (failed) {
    return (
      <p role="alert" className="text-sm text-destructive">
        This sheet could not be opened. It may still be being read.
      </p>
    );
  }

  const highlighted = [
    ...index.marks.filter((mark) => selected.has(mark.id)),
    ...(hovered && !selected.has(hovered.id) ? [hovered] : []),
  ];

  return (
    <div
      className={`relative overflow-hidden rounded-lg border bg-neutral-100 dark:bg-neutral-900 ${className ?? "h-[calc(100vh-9rem)] min-h-[32rem] w-full"}`}
    >
      <div ref={container} data-testid="sheet-viewer" className="absolute inset-0" />
      <canvas
        ref={canvas}
        data-testid="overlay-canvas"
        className="pointer-events-none absolute inset-0"
      />
      {transform && (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          data-testid="overlay-highlights"
        >
          {highlighted.map((mark) => (
            <Highlight
              key={mark.id}
              mark={mark}
              transform={transform}
              selected={selected.has(mark.id)}
            />
          ))}
          {lasso && lasso.length > 1 && (
            <polygon
              points={lasso.map(([x, y]) => toScreen(transform, x!, y!).join(",")).join(" ")}
              fill="rgba(37,99,235,0.08)"
              stroke="#2563eb"
              strokeDasharray="4 3"
            />
          )}
          {tool === "length" && path.length > 0 && (
            <polyline
              points={path.map(([x, y]) => toScreen(transform, x!, y!).join(",")).join(" ")}
              fill="none"
              stroke="#ea580c"
              strokeWidth={2}
              strokeDasharray="6 4"
            />
          )}
        </svg>
      )}
    </div>
  );
}

function Highlight({
  mark,
  transform,
  selected,
}: {
  mark: Mark;
  transform: Transform;
  selected: boolean;
}) {
  const colour = STATUS_COLOURS[mark.status] ?? "#6b7280";
  const title = `${mark.item_human_id ?? "not taken off"} · ${mark.object_type} · ${mark.status}`;
  if (mark.points.length >= 2) {
    return (
      <polyline
        points={mark.points.map(([x, y]) => toScreen(transform, x!, y!).join(",")).join(" ")}
        fill="none"
        stroke={colour}
        strokeWidth={selected ? 4 : 3}
        data-mark-id={mark.id}
      >
        <title>{title}</title>
      </polyline>
    );
  }
  const [x0, y0] = toScreen(transform, mark.box[0]!, mark.box[1]!);
  const [x1, y1] = toScreen(transform, mark.box[2]!, mark.box[3]!);
  return (
    <rect
      x={x0 - 2}
      y={y0 - 2}
      width={x1 - x0 + 4}
      height={y1 - y0 + 4}
      fill="none"
      stroke={colour}
      strokeWidth={selected ? 3 : 2}
      strokeDasharray={selected ? undefined : "3 2"}
      data-mark-id={mark.id}
    >
      <title>{title}</title>
    </rect>
  );
}

import {
  BAND_STYLE,
  type Layers,
  type Mark,
  type MarkIndex,
  STATUS_COLOURS,
  type Transform,
  toScreen,
  viewBox,
  visible,
} from "./marks";

/**
 * Draw the marks the viewport can see onto a 2D canvas (ADR-005's overlay).
 *
 * The index culls to the viewport first, so a frame costs what is on screen, not what is on
 * the sheet. Symbols are boxes, runs are polylines, manual lengths are dashed. Selected and
 * hovered marks are not drawn here: the SVG layer draws them crisp on top.
 *
 * Returns how many marks were drawn, for the performance bench.
 */
export function drawMarks(
  context: CanvasRenderingContext2D,
  index: MarkIndex,
  transform: Transform,
  size: { width: number; height: number },
  layers: Layers,
  skip: ReadonlySet<string>,
): number {
  context.clearRect(0, 0, size.width, size.height);
  const inView = index.within(viewBox(transform, size.width, size.height));
  // Tiny on screen: a dot is all that can be seen, and dots are cheap.
  const tiny = transform.scale < 0.6;
  let drawn = 0;
  for (const mark of inView) {
    if (skip.has(mark.id) || !visible(mark, layers)) continue;
    drawMark(context, mark, transform, tiny);
    drawn += 1;
  }
  return drawn;
}

function drawMark(
  context: CanvasRenderingContext2D,
  mark: Mark,
  transform: Transform,
  tiny: boolean,
): void {
  const colour = STATUS_COLOURS[mark.status] ?? "#6b7280";
  const style = BAND_STYLE[mark.band] ?? BAND_STYLE.low!;
  context.strokeStyle = colour;
  context.fillStyle = colour;
  context.lineWidth = style.width;
  if (mark.points.length >= 2) {
    context.setLineDash(mark.kind === "manual" ? [6, 4] : []);
    context.beginPath();
    mark.points.forEach(([x, y], i) => {
      const [px, py] = toScreen(transform, x!, y!);
      if (i === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    });
    context.stroke();
    return;
  }
  const [x0, y0] = toScreen(transform, mark.box[0]!, mark.box[1]!);
  const [x1, y1] = toScreen(transform, mark.box[2]!, mark.box[3]!);
  if (tiny) {
    context.fillRect((x0 + x1) / 2 - 1.5, (y0 + y1) / 2 - 1.5, 3, 3);
    return;
  }
  context.setLineDash([]);
  context.globalAlpha = style.fill;
  context.fillRect(x0, y0, x1 - x0, y1 - y0);
  context.globalAlpha = 1;
  context.strokeRect(x0, y0, x1 - x0, y1 - y0);
}

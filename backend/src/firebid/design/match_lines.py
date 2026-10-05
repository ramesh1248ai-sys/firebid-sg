"""A sheet's match lines, and the side of each the sheet answers for (FR-DSN-06).

A floor too large for one sheet is drawn on several, cut at match lines: a long line across
the plan, labelled "MATCH LINE" and usually naming the sheet that continues it. Each sheet
shows a little of the floor beyond its line, so a layout made on every sheet whole designs
the shared strip twice.

1. **The label.** A text span reading "MATCH LINE", or a note that the floor goes on
   elsewhere ("FOR CONTINUATION, REFER TO DRG. NO. ..."). The sheet it names may be in the
   same words or in a drawing number written beside them.
2. **Its line.** A straight line beside the label that runs across a good part of the plan.
   A real plan has many: walls, pipes and grid lines pass every label. So, in this order: a
   line of the structural grid is taken only when no other is beside the label (a floor cut
   on a grid line); a line running the way the words are written is taken before one
   crossing them, as the note is written along its line; a line of regular dashes, as a
   match line is usually drawn, before a solid one; then the longest of those nearest.
3. **The sheet's side.** The side with more of the pipework the platform found drawn: a
   sheet draws its own part in full and only enough beyond to show where it continues.
   Where no pipe was found, the side with more coloured linework (the services, on a grey
   base plan); with neither, the larger side.

The scope is the plan area on the sheet's side of every match line. It is a proposal, shown
with the words and the reason it rests on, and a person may take the other side or the whole
sheet.

Pure: geometry in, lines and a polygon out. Sheet millimetres, y down.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa

from firebid.drawings import geometry

Box = tuple[float, float, float, float]
Point = tuple[float, float]
Polygon = list[Point]

# How a drawing marks the cut: "MATCH LINE", or a note along the line saying where the floor
# goes on ("FOR CONTINUATION, REFER TO DRG. NO. ...", "CONTINUED ON ...").
LABEL = re.compile(
    r"\bMATCH\s*-?\s*LINE\b|\bFOR\s+CONTINUATION\b|\bCONTINU(?:ED|ES|ATION)\s+ON\b",
    re.IGNORECASE,
)
CONTINUES = re.compile(
    r"\b(?:SEE|REFER(?:\s+TO)?|CONT(?:INUED|INUATION|\.)?(?:\s+ON)?)\s+"
    r"(?:(?:DWG|DRG|DRAWING|SHEET)\.?\s*)?(?:NO\.?\s*)?([A-Z0-9][A-Z0-9/_.()-]*\d[A-Z0-9/_.()-]*)",
    re.IGNORECASE,
)
# A match line reaches across at least this share of the plan's smaller side.
MIN_SPAN = 0.2
MIN_SPAN_MM = 40.0
# Its label is on it, or beside it: no further than this (sheet mm).
NEAR_MM = 10.0
# Lines this close to the nearest are taken as equally near; the longest is the match line.
TIE_MM = 2.0
# A line runs the way the words are written when it is within this of their direction.
PARALLEL_DEG = 10.0
# Regular dashes: at least this many pieces, most of them about the same length.
DASHES = 8
DASH_SPREAD = (0.6, 1.6)  # of the mean piece
DASH_SHARE = 0.8
# A drawing number: letters and figures in at least three parts, with a figure in it.
DRAWING_NUMBER = re.compile(r"^[A-Z0-9]+(?:[-_/.][A-Z0-9]+){2,}$", re.IGNORECASE)
# Drawn pieces over extent: a dash-dot line covers about half of its length.
MIN_COVER = 0.25
# max(r, g, b) - min(r, g, b) above this is a colour (the services), not a grey.
SATURATED = 40


@dataclass(frozen=True)
class MatchLine:
    """One match line across the plan area, and the side this sheet answers for."""

    label: str
    other_sheet: str | None
    a: Point
    b: Point
    # +1: the side where (b - a) x (p - a) is positive; -1: the other.
    side: int
    reason: str
    services_mm: tuple[float, float]  # coloured linework on the +1 side and on the -1 side

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "other_sheet": self.other_sheet,
            "line": [[round(v, 2) for v in self.a], [round(v, 2) for v in self.b]],
            "side": self.side,
            "proposed_side": self.side,
            "reason": self.reason,
            "services_mm": [round(v, 1) for v in self.services_mm],
        }


@dataclass
class Found:
    lines: list[MatchLine]
    # Labels whose line could not be found: named, so a person sets the scope by hand.
    unplaced: list[str]


def _cross(a: Point, b: Point, x: Any, y: Any) -> Any:
    return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])


def _is_colour(colour: int) -> bool:
    if colour < 0:
        return False
    red, green, blue = (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF
    return max(red, green, blue) - min(red, green, blue) > SATURATED


def _clip_to_box(theta: float, offset: float, region: Box) -> tuple[Point, Point] | None:
    """The infinite line at this direction and offset, cut to the region."""
    x0, y0, x1, y1 = region
    cos, sin = math.cos(theta), math.sin(theta)
    origin = (-offset * sin, offset * cos)
    low, high = -math.inf, math.inf
    for start, step, lower, upper in ((origin[0], cos, x0, x1), (origin[1], sin, y0, y1)):
        if abs(step) < 1e-12:
            if not lower <= start <= upper:
                return None
            continue
        first, second = sorted(((lower - start) / step, (upper - start) / step))
        low, high = max(low, first), min(high, second)
    if not low < high:
        return None
    return (
        (origin[0] + low * cos, origin[1] + low * sin),
        (origin[0] + high * cos, origin[1] + high * sin),
    )


Line = tuple[float, float, float, float, float, bool]


def _straight_lines(seg: geometry.Segments, wanted: np.ndarray, shift: float) -> list[Line]:
    """Segments grouped by the infinite line they lie on.

    Each as (theta, offset, low, high, drawn, dashed): its direction, its offset from the
    origin, where along itself it starts and ends, how much of it is drawn, and whether it
    is drawn as regular dashes.
    """
    rows = np.flatnonzero(wanted)
    if rows.size == 0:
        return []
    x0, y0, x1, y1 = seg.x0[rows], seg.y0[rows], seg.x1[rows], seg.y1[rows]
    angle = np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180.0
    # A line a hair under 180 degrees is the same line as one a hair over 0.
    angle = np.where(angle > 179.75, angle - 180.0, angle)
    angle_bin = np.floor(angle * 2 + shift)  # half a degree, for grouping only
    # Offsets by each piece's own direction: pieces of one line share it, so they share an
    # offset however far apart along the line they are.
    theta = np.radians(angle)
    middle_x, middle_y = (x0 + x1) / 2, (y0 + y1) / 2
    offset = -middle_x * np.sin(theta) + middle_y * np.cos(theta)
    along0 = x0 * np.cos(theta) + y0 * np.sin(theta)
    along1 = x1 * np.cos(theta) + y1 * np.sin(theta)
    keys = angle_bin.astype(np.int64) * 100_000_000 + np.floor(offset + shift).astype(np.int64)
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
    lows = np.minimum.reduceat(np.minimum(along0, along1)[order], starts)
    highs = np.maximum.reduceat(np.maximum(along0, along1)[order], starts)
    lengths = np.hypot(x1 - x0, y1 - y0)[order]
    drawn = np.add.reduceat(lengths, starts)
    pieces = np.diff(np.r_[starts, order.size])
    offsets = np.add.reduceat(offset[order], starts) / pieces
    thetas = theta[order][starts]
    # Regular dashes: many pieces, most of them about as long as the mean piece.
    mean = np.repeat(drawn / pieces, pieces)
    alike = (lengths >= DASH_SPREAD[0] * mean) & (lengths <= DASH_SPREAD[1] * mean)
    dashed = (pieces >= DASHES) & (
        np.add.reduceat(alike.astype(np.int64), starts) >= DASH_SHARE * pieces
    )
    return [
        (float(t), float(o), float(lo), float(hi), float(d), bool(regular))
        for t, o, lo, hi, d, regular in zip(
            thetas, offsets, lows, highs, drawn, dashed, strict=True
        )
    ]


def _written_at(span: dict[str, Any]) -> float:
    """The direction the words run, in degrees from 0 to 180 (0: across the sheet)."""
    rotation = float(span.get("rotation") or 0.0) % 180.0
    if rotation:
        return rotation
    wide = float(span["maxx"]) - float(span["minx"])
    tall = float(span["maxy"]) - float(span["miny"])
    return 90.0 if tall > wide else 0.0


def _runs_with(theta: float, written: float) -> bool:
    apart = abs(math.degrees(theta) % 180.0 - written) % 180.0
    return min(apart, 180.0 - apart) <= PARALLEL_DEG


def _named_beside(spans: list[dict[str, Any]], label: dict[str, Any]) -> str | None:
    """A drawing number written beside the label, in line with it: the sheet it means."""
    left, top = float(label["minx"]), float(label["miny"])
    right, bottom = float(label["maxx"]), float(label["maxy"])
    upright = _written_at(label) == 90.0
    best: tuple[float, str] | None = None
    for span in spans:
        words = "".join(str(span.get("text") or "").split())
        if span is label or not DRAWING_NUMBER.match(words) or not any(c.isdigit() for c in words):
            continue
        x0, y0, x1, y1 = (float(span[k]) for k in ("minx", "miny", "maxx", "maxy"))
        # In line with the label (across its writing direction), and not far along it.
        across = max(x0 - right, left - x1, 0.0) if upright else max(y0 - bottom, top - y1, 0.0)
        along = max(y0 - bottom, top - y1, 0.0) if upright else max(x0 - right, left - x1, 0.0)
        if across <= NEAR_MM and along <= 15 * NEAR_MM and (best is None or along < best[0]):
            best = (along, words)
    return best[1] if best else None


def _is_grid(theta: float, offset: float, grid: list[tuple[str, float]]) -> bool:
    """Whether a line lies on a line of the structural grid ("x": vertical at x)."""
    degrees = math.degrees(theta)
    for axis, position in grid:
        if axis == "x" and abs(degrees - 90.0) < 0.5 and abs(-offset - position) <= 1.0:
            return True
        level = min(abs(degrees), abs(degrees - 180.0)) < 0.5
        if axis == "y" and level and abs(offset - position) <= 1.0:
            return True
    return False


def _pipe_sides(a: Point, b: Point, services: list[list[Point]]) -> tuple[float, float]:
    """Drawn pipe on the +1 side and on the -1 side of the line, by each piece's middle."""
    positive = negative = 0.0
    for run in services:
        for (px, py), (qx, qy) in itertools.pairwise(run):
            signed = float(_cross(a, b, (px + qx) / 2, (py + qy) / 2))
            length = math.hypot(qx - px, qy - py)
            if signed > 0:
                positive += length
            elif signed < 0:
                negative += length
    return positive, negative


def find(
    table: pa.Table,
    region: Box,
    *,
    grid: list[tuple[str, float]] | None = None,
    services: list[list[Point]] | None = None,
) -> Found:
    """The match lines across `region` (the plan view, sheet mm), each with its side.

    `grid` is the view's structural grid, as (axis, position) with "x" a vertical line at
    that x; `services` are the pipe runs found drawn on the sheet.
    """
    spans = geometry.texts(table)
    labels = [span for span in spans if span.get("text") and LABEL.search(str(span["text"]))]
    if not labels:
        return Found([], [])
    x0, y0, x1, y1 = region
    seg = geometry.segments(table)
    if len(seg) == 0:
        return Found([], [str(span["text"]).strip() for span in labels])
    middle_x, middle_y = (seg.x0 + seg.x1) / 2, (seg.y0 + seg.y1) / 2
    inside = (middle_x >= x0) & (middle_x <= x1) & (middle_y >= y0) & (middle_y <= y1)
    span_needed = max(MIN_SPAN_MM, MIN_SPAN * min(x1 - x0, y1 - y0))
    # Binned twice, half a bin apart, so a line whose pieces fall either side of a bin's edge
    # is still one line in one of the two.
    candidates = [
        line
        for shift in (0.0, 0.5)
        for line in _straight_lines(seg, inside & (seg.lengths > 0.05), shift)
        if line[3] - line[2] >= span_needed and line[4] >= MIN_COVER * (line[3] - line[2])
    ]
    colours = np.asarray(table.column("color").to_pylist(), dtype=object)[seg.row]
    coloured = inside & np.fromiter(
        (c is not None and _is_colour(int(c)) for c in colours), dtype=bool, count=len(seg)
    )

    lines: list[MatchLine] = []
    unplaced: list[str] = []
    taken: list[tuple[float, float]] = []
    for span in labels:
        words = " ".join(str(span["text"]).split())
        left, top = float(span["minx"]), float(span["miny"])
        right, bottom = float(span["maxx"]), float(span["maxy"])
        centre = ((left + right) / 2, (top + bottom) / 2)
        corners = ((left, top), (right, top), (right, bottom), (left, bottom))
        beside: list[tuple[float, Line]] = []
        on_grid: list[tuple[float, Line]] = []
        for line in candidates:
            theta, offset, low, high, _, _ = line
            # From the label's box, not its middle: words written across a line are as near
            # to it as words written along it.
            away = [-x * math.sin(theta) + y * math.cos(theta) - offset for x, y in corners]
            distance = 0.0 if min(away) < 0 < max(away) else min(abs(v) for v in away)
            along = centre[0] * math.cos(theta) + centre[1] * math.sin(theta)
            if distance <= NEAR_MM and low - 2 * NEAR_MM <= along <= high + 2 * NEAR_MM:
                (on_grid if _is_grid(theta, offset, grid or []) else beside).append(
                    (distance, line)
                )
        near = beside or on_grid
        if not near:
            unplaced.append(words)
            continue
        written = _written_at(span)
        near = [item for item in near if _runs_with(item[1][0], written)] or near
        near = [item for item in near if item[1][5]] or near
        nearest = min(distance for distance, _ in near)
        theta, offset, _, _, _, _ = max(
            (line for distance, line in near if distance <= nearest + TIE_MM),
            key=lambda line: line[3] - line[2],
        )
        # Two labels on one line, or one line found in both binnings: one match line.
        if any(
            abs(math.degrees(theta) - a) < 1.0 and abs(offset - o) < 2 * TIE_MM for a, o in taken
        ):
            continue
        ends = _clip_to_box(theta, offset, region)
        if ends is None:
            unplaced.append(words)
            continue
        taken.append((math.degrees(theta), offset))
        a, b = ends
        positive, negative = _pipe_sides(a, b, services or [])
        reason = "the side with more of the pipework drawn"
        if positive == negative:
            signed = _cross(a, b, middle_x[coloured], middle_y[coloured])
            lengths = seg.lengths[coloured]
            positive = float(lengths[signed > 0].sum())
            negative = float(lengths[signed < 0].sum())
            reason = "the side with more of the services drawn"
        if positive != negative:
            side = 1 if positive > negative else -1
        else:
            whole = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            side = 1 if _area(_clip(whole, a, b, 1)) >= _area(_clip(whole, a, b, -1)) else -1
            reason = "the larger side: no services are drawn on either"
        other = CONTINUES.search(words)
        lines.append(
            MatchLine(
                label=words,
                other_sheet=other.group(1).rstrip(".") if other else _named_beside(spans, span),
                a=a,
                b=b,
                side=side,
                reason=reason,
                services_mm=(positive, negative),
            )
        )
    return Found(lines, unplaced)


def _clip(polygon: Polygon, a: Point, b: Point, side: int) -> Polygon:
    """The part of a polygon on one side of the line through a and b."""
    out: Polygon = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        here = side * float(_cross(a, b, current[0], current[1]))
        before = side * float(_cross(a, b, previous[0], previous[1]))
        if (here >= 0) != (before >= 0):
            share = before / (before - here)
            out.append(
                (
                    previous[0] + share * (current[0] - previous[0]),
                    previous[1] + share * (current[1] - previous[1]),
                )
            )
        if here >= 0:
            out.append(current)
    return out


def _area(polygon: Polygon) -> float:
    return abs(
        sum(
            polygon[index - 1][0] * point[1] - point[0] * polygon[index - 1][1]
            for index, point in enumerate(polygon)
        )
        / 2
    )


def scope(region: Box, lines: list[tuple[Point, Point, int]]) -> Polygon | None:
    """The plan area on the sheet's side of every match line. None: no line, the whole view."""
    if not lines:
        return None
    x0, y0, x1, y1 = region
    polygon: Polygon = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for a, b, side in lines:
        polygon = _clip(polygon, a, b, side)
        if len(polygon) < 3:
            return None
    return polygon if _area(polygon) > 1.0 else None


def scope_from_json(region: Box, lines: list[dict[str, Any]]) -> Polygon | None:
    """The scope from stored match lines, each with the side now chosen for it."""
    return scope(
        region,
        [
            (
                (float(line["line"][0][0]), float(line["line"][0][1])),
                (float(line["line"][1][0]), float(line["line"][1][1])),
                int(line["side"]),
            )
            for line in lines
        ],
    )

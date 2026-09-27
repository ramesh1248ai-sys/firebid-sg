"""The structural grid: where on the building a point is (FR-VIS-07).

A grid line is a long straight line with a bubble at its end, a circle with a short label in
it: letters across, numbers up, on Singapore drawings. Found from the geometry alone, so a DXF
and its PDF export give the same grid.

With a grid, any point on the sheet has a reference an estimator recognises: "Grid B2" on an
intersection, "Grid A1-B2" inside a bay (with an en dash). The grid is also a coordinate
system shared between sheets. An enlarged plan and the general arrangement it repeats sit
on the same gridlines whatever their scales, which is how their overlap is found (FR-VIS-08).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import pyarrow as pa

from firebid.drawings.geometry import segments, texts

LABEL = re.compile(r"^[A-Z]{1,2}'?$|^\d{1,3}'?$")
# A bubble is between these diameters on paper.
BUBBLE_MIN_MM, BUBBLE_MAX_MM = 4.0, 25.0
# A grid line is at least this long, and within this angle of the page axes.
LINE_MIN_MM = 40.0
SQUARE_DEGREES = 1.0
# Between the two corners of a bay: an en dash, as drawings write it.
BAY = "\N{EN DASH}"


@dataclass(frozen=True)
class GridLine:
    label: str
    axis: str  # "x": a vertical line at an x position; "y": a horizontal line at a y position
    position: float  # sheet mm


@dataclass(frozen=True)
class GridSystem:
    across: tuple[GridLine, ...]  # vertical lines, left to right
    up: tuple[GridLine, ...]  # horizontal lines, in label order

    def as_json(self) -> dict[str, list[list[object]]]:
        return {
            "across": [[line.label, round(line.position, 3)] for line in self.across],
            "up": [[line.label, round(line.position, 3)] for line in self.up],
        }

    @classmethod
    def from_json(cls, data: dict[str, list[list[object]]]) -> GridSystem:
        def lines(axis: str, rows: list[list[object]]) -> tuple[GridLine, ...]:
            return tuple(
                GridLine(str(label), axis, float(str(position))) for label, position in rows
            )

        return cls(lines("x", data.get("across", [])), lines("y", data.get("up", [])))

    def reference(self, x: float, y: float, tolerance: float = 1.0) -> str | None:
        """`Grid B2` on an intersection, `Grid A1-B2` (en dash) in a bay, None off the grid."""
        across = _between(self.across, x, tolerance)
        up = _between(self.up, y, tolerance)
        if across is None or up is None:
            return None
        (left, right), (low, high) = across, up
        if left == right and low == high:
            return f"Grid {left}{low}"
        return f"Grid {left}{low}{BAY}{right}{high}"

    def index(self, x: float, y: float) -> tuple[float, float] | None:
        """A point in grid units, counted by label: 1.0 on A (or 1), 2.0 on B, 1.5 between.

        Counted by label, not by line, so two sheets that show different parts of one grid
        give the same coordinates for the same place.
        """
        gx = _fraction(self.across, x)
        gy = _fraction(self.up, y)
        return None if gx is None or gy is None else (gx, gy)

    def box(
        self, extent: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float] | None:
        """A sheet rectangle in grid units, low corner first."""
        low, high = self.index(extent[0], extent[1]), self.index(extent[2], extent[3])
        if low is None or high is None:
            return None
        (ax, ay), (bx, by) = low, high
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """How much of grid box `b` lies inside grid box `a`, from 0 to 1."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    area = (b[2] - b[0]) * (b[3] - b[1])
    if width <= 0 or height <= 0 or area <= 0:
        return 0.0
    return width * height / area


def _sorted(lines: tuple[GridLine, ...]) -> list[GridLine]:
    """Lines in label order: A, B ... Z, AA; 1, 2 ... 10. Not the order they are drawn in."""

    return sorted(lines, key=lambda line: (ordinal(line.label), line.label))


def ordinal(label: str) -> int:
    """A label's place in its sequence: A and 1 are 1, B and 2 are 2, AA follows Z."""
    label = label.rstrip("'")
    if label.isdigit():
        return int(label)
    return sum((ord(c) - 64) * 26**i for i, c in enumerate(reversed(label)))


def _between(lines: tuple[GridLine, ...], value: float, tolerance: float) -> tuple[str, str] | None:
    ordered = _sorted(lines)
    if not ordered:
        return None
    for line in ordered:
        if abs(line.position - value) <= tolerance:
            return line.label, line.label
    for first, second in pairwise(ordered):
        low, high = sorted((first.position, second.position))
        if low < value < high:
            return first.label, second.label
    return None


def _fraction(lines: tuple[GridLine, ...], value: float) -> float | None:
    ordered = _sorted(lines)
    if len(ordered) < 2:
        return None
    positions = [line.position for line in ordered]
    ordinals = [ordinal(line.label) for line in ordered]
    pairs = list(zip(pairwise(positions), pairwise(ordinals), strict=True))
    for (first, second), (low, high) in pairs:
        if min(first, second) <= value <= max(first, second) and first != second:
            return low + (high - low) * (value - first) / (second - first)
    # Beyond the outer lines: extend the nearest bay.
    (first, second), (low, high) = (
        pairs[0] if abs(value - positions[0]) < abs(value - positions[-1]) else pairs[-1]
    )
    if first == second:
        return None
    return low + (high - low) * (value - first) / (second - first)


def detect(table: pa.Table) -> GridSystem | None:
    """The grid on a sheet, or None when it has none worth the name (two lines each way)."""
    bubbles = _bubbles(table)
    if not bubbles:
        return None
    lines = segments(table)
    if len(lines) == 0:
        return None
    lengths = lines.lengths
    dx, dy = lines.x1 - lines.x0, lines.y1 - lines.y0
    vertical = (np.abs(dx) <= np.tan(np.radians(SQUARE_DEGREES)) * np.abs(dy)) & (
        lengths >= LINE_MIN_MM
    )
    horizontal = (np.abs(dy) <= np.tan(np.radians(SQUARE_DEGREES)) * np.abs(dx)) & (
        lengths >= LINE_MIN_MM
    )

    across: dict[str, GridLine] = {}
    up: dict[str, GridLine] = {}
    for label, cx, cy, radius in bubbles:
        reach = 3 * radius
        # A vertical line on the bubble's x, ending near it.
        candidates = np.flatnonzero(
            vertical
            & (np.abs((lines.x0 + lines.x1) / 2 - cx) <= radius * 0.3)
            & (np.minimum(np.abs(lines.y0 - cy), np.abs(lines.y1 - cy)) <= reach)
        )
        if candidates.size and label not in across:
            best = candidates[np.argmax(lengths[candidates])]
            across[label] = GridLine(label, "x", float((lines.x0[best] + lines.x1[best]) / 2))
            continue
        candidates = np.flatnonzero(
            horizontal
            & (np.abs((lines.y0 + lines.y1) / 2 - cy) <= radius * 0.3)
            & (np.minimum(np.abs(lines.x0 - cx), np.abs(lines.x1 - cx)) <= reach)
        )
        if candidates.size and label not in up:
            best = candidates[np.argmax(lengths[candidates])]
            up[label] = GridLine(label, "y", float((lines.y0[best] + lines.y1[best]) / 2))

    if len(across) < 2 or len(up) < 2:
        return None
    return GridSystem(tuple(_sorted(tuple(across.values()))), tuple(_sorted(tuple(up.values()))))


def _bubbles(table: pa.Table) -> list[tuple[str, float, float, float]]:
    """Grid bubbles: a round outline of bubble size with a grid label inside."""
    rows = table.select(
        ["kind", "closed", "cx", "cy", "radius", "minx", "miny", "maxx", "maxy"]
    ).to_pylist()
    rounds: list[tuple[float, float, float]] = []
    for row in rows:
        if row["kind"] == "circle" and row["radius"]:
            if BUBBLE_MIN_MM <= 2 * row["radius"] <= BUBBLE_MAX_MM:
                rounds.append((row["cx"], row["cy"], row["radius"]))
        elif row["kind"] == "polyline" and row["closed"] and row["minx"] is not None:
            width, height = row["maxx"] - row["minx"], row["maxy"] - row["miny"]
            # A PDF circle is a closed curve whose box is square.
            if BUBBLE_MIN_MM <= width <= BUBBLE_MAX_MM and math.isclose(
                width, height, rel_tol=0.05
            ):
                rounds.append(
                    ((row["minx"] + row["maxx"]) / 2, (row["miny"] + row["maxy"]) / 2, width / 2)
                )
    if not rounds:
        return []
    centres = np.asarray(rounds)
    found = []
    for span in texts(table):
        label = (span["text"] or "").strip().upper()
        if not LABEL.match(label):
            continue
        tx, ty = (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2
        distance = np.hypot(centres[:, 0] - tx, centres[:, 1] - ty)
        nearest = int(np.argmin(distance))
        if distance[nearest] <= centres[nearest, 2] * 0.8:
            cx, cy, radius = (float(value) for value in centres[nearest])
            found.append((label, cx, cy, radius))
    return found

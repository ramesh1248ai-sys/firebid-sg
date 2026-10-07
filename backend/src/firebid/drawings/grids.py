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
from typing import Any

import numpy as np
import pyarrow as pa

from firebid.drawings.geometry import segments, texts

# A gridline's label: letters (B, AC), a number (12), or a letter and a number (A12), which
# a building with several blocks uses for the lines of one of them.
LABEL = re.compile(r"^[A-Z]{1,2}'?$|^\d{1,3}'?$|^[A-Z]\d{1,2}'?$")
# A bubble is between these diameters on paper.
BUBBLE_MIN_MM, BUBBLE_MAX_MM = 4.0, 25.0
# A grid line is at least this long, and within this angle of the page axes.
LINE_MIN_MM = 40.0
SQUARE_DEGREES = 1.0
# Between the two corners of a bay: an en dash, as drawings write it.
BAY = "\N{EN DASH}"


# A point this far past the drawn end of a gridline is still beside it.
PAST_THE_END_MM = 10.0


@dataclass(frozen=True)
class GridLine:
    """One gridline. Square to the sheet it is a position; skewed, it leans.

    `position` is where the line is at `origin` on the other axis, and `slope` how far it
    moves for each millimetre along that axis: a line up the sheet (axis "x") is at
    x = `position` when y = `origin`. A line square to the sheet has no slope.

    `low` and `high` are how far along the other axis the line is drawn, where that is
    known: a wing of a building has its own gridlines, and they say nothing of a point
    beyond their ends.
    """

    label: str
    axis: str  # "x": a line up the sheet, placed by its x; "y": a line across it, by its y
    position: float  # sheet mm
    slope: float = 0.0
    origin: float = 0.0
    low: float | None = None
    high: float | None = None

    def at(self, other: float) -> float:
        """Where the line is, at this place along the other axis."""
        return self.position + self.slope * (other - self.origin)

    def reaches(self, other: float) -> bool:
        """Whether the line is drawn as far as this place along the other axis."""
        if self.low is None or self.high is None:
            return True
        return self.low - PAST_THE_END_MM <= other <= self.high + PAST_THE_END_MM


def _stored(line: GridLine) -> list[object]:
    if line.low is None or line.high is None:
        return [line.label, round(line.position, 3)]
    return [
        line.label,
        round(line.position, 3),
        round(line.slope, 6),
        round(line.origin, 3),
        round(line.low, 3),
        round(line.high, 3),
    ]


@dataclass(frozen=True)
class GridSystem:
    across: tuple[GridLine, ...]  # lines up the sheet, in label order
    up: tuple[GridLine, ...]  # lines across the sheet, in label order
    # A second grid on the sheet with fewer lines: a wing's, beside the main block's. A
    # point among its lines is named by them; elsewhere by the grid above.
    local: GridSystem | None = None

    @property
    def one_grid(self) -> bool:
        """Whether this is one grid over the whole sheet: gridlines found as long lines.

        Lines found dashed carry how far they are drawn, and may be one grid of several on
        the sheet (a building and its skewed wing). They name a place; they are not a
        coordinate system shared between sheets, so they give no index.
        """
        return self.local is None and all(line.low is None for line in (*self.across, *self.up))

    def as_json(self) -> dict[str, list[list[object]]]:
        """Label and position of each line. A line found dashed has its slope, its origin
        and how far it is drawn after them. A second grid's lines are under `local_across`
        and `local_up`."""
        stored = {
            "across": [_stored(line) for line in self.across],
            "up": [_stored(line) for line in self.up],
        }
        if self.local is not None:
            stored["local_across"] = [_stored(line) for line in self.local.across]
            stored["local_up"] = [_stored(line) for line in self.local.up]
        return stored

    @classmethod
    def from_json(cls, data: dict[str, list[list[object]]]) -> GridSystem:
        def lines(axis: str, rows: list[list[object]]) -> tuple[GridLine, ...]:
            return tuple(
                GridLine(str(row[0]), axis, *(float(str(value)) for value in row[1:6]))
                for row in rows
            )

        local = None
        if data.get("local_across") and data.get("local_up"):
            local = cls(lines("x", data["local_across"]), lines("y", data["local_up"]))
        return cls(lines("x", data.get("across", [])), lines("y", data.get("up", [])), local)

    def reference(self, x: float, y: float, tolerance: float = 1.0) -> str | None:
        """`Grid B2` on an intersection, `Grid A1-B2` (en dash) in a bay, None off the grid.

        Letters one way and numbers the other are written together. Any other pair
        of labels is parted by a stroke: `Grid AA/K`, `Grid AC/A4`.
        """
        if self.local is not None:
            named = self.local.reference(x, y, tolerance)
            if named is not None:
                return named
        across = _between(self.across, x, tolerance, y)
        up = _between(self.up, y, tolerance, x)
        if across is None or up is None:
            return None
        (left, right), (low, high) = across, up
        stroke = "" if {kind(left), kind(low)} == {"A", "1"} else "/"
        if left == right and low == high:
            return f"Grid {left}{stroke}{low}"
        return f"Grid {left}{stroke}{low}{BAY}{right}{stroke}{high}"

    def index(self, x: float, y: float) -> tuple[float, float] | None:
        """A point in grid units, counted by label: 1.0 on A (or 1), 2.0 on B, 1.5 between.

        Counted by label, not by line, so two sheets that show different parts of one grid
        give the same coordinates for the same place. None where the sheet's gridlines may
        not be one grid (`one_grid`): two places would share an index, and the takeoff
        counts two things at one index once.
        """
        if not self.one_grid:
            return None
        gx = _fraction(self.across, x, y)
        gy = _fraction(self.up, y, x)
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
    """A label's place in its sequence: A and 1 are 1, B and 2 are 2, AA follows Z; A12 is
    12 among the lines lettered A."""
    label = label.rstrip("'")
    if label.isdigit():
        return int(label)
    if label.isalpha():
        return sum((ord(c) - 64) * 26**i for i, c in enumerate(reversed(label)))
    return int(label.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def kind(label: str) -> str:
    """What sort of label this is: "A" for letters, "1" for a number, and the letter with
    "1" for a letter and a number ("A1" for A4 and A12). The gridlines that run one way
    are labelled one way."""
    label = label.rstrip("'")
    if label.isdigit():
        return "1"
    if label.isalpha():
        return "A"
    return label.rstrip("0123456789") + "1"


def _between(
    lines: tuple[GridLine, ...], value: float, tolerance: float, other: float = 0.0
) -> tuple[str, str] | None:
    """The line a value is on, or the two it is between, of the lines drawn that far."""
    ordered = [line for line in _sorted(lines) if line.reaches(other)]
    if not ordered:
        return None
    for line in ordered:
        if abs(line.at(other) - value) <= tolerance:
            return line.label, line.label
    for first, second in pairwise(ordered):
        low, high = sorted((first.at(other), second.at(other)))
        if low < value < high:
            return first.label, second.label
    return None


def _fraction(lines: tuple[GridLine, ...], value: float, other: float = 0.0) -> float | None:
    ordered = _sorted(lines)
    if len(ordered) < 2:
        return None
    positions = [line.at(other) for line in ordered]
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


def detect(
    table: pa.Table, bubbles: list[tuple[str, float, float, float]] | None = None
) -> GridSystem | None:
    """The grid on a sheet, or None when it has none worth the name (two lines each way).

    A gridline drawn as one long line ending at its bubble is found first, as it always
    was. Where that finds no grid, the lines are looked for as a real plan draws them:
    dashed, and in a skewed wing at any angle (`_dashed`).
    """
    if bubbles is None:
        bubbles = _bubbles(table)
    if not bubbles:
        return None
    lines = segments(table)
    if len(lines) == 0:
        return None
    lengths = lines.lengths
    dx, dy = lines.x1 - lines.x0, lines.y1 - lines.y0
    # The long lines square to the sheet, picked out once: a real sheet has two million
    # segments and a few hundred of these, and every bubble was looking through them all.
    tall = np.flatnonzero(
        (np.abs(dx) <= np.tan(np.radians(SQUARE_DEGREES)) * np.abs(dy)) & (lengths >= LINE_MIN_MM)
    )
    wide = np.flatnonzero(
        (np.abs(dy) <= np.tan(np.radians(SQUARE_DEGREES)) * np.abs(dx)) & (lengths >= LINE_MIN_MM)
    )
    tall_x, tall_y0, tall_y1 = (lines.x0[tall] + lines.x1[tall]) / 2, lines.y0[tall], lines.y1[tall]
    wide_y, wide_x0, wide_x1 = (lines.y0[wide] + lines.y1[wide]) / 2, lines.x0[wide], lines.x1[wide]

    across: dict[str, GridLine] = {}
    up: dict[str, GridLine] = {}
    for label, cx, cy, radius in bubbles:
        reach = 3 * radius
        # A vertical line on the bubble's x, ending near it.
        candidates = np.flatnonzero(
            (np.abs(tall_x - cx) <= radius * 0.3)
            & (np.minimum(np.abs(tall_y0 - cy), np.abs(tall_y1 - cy)) <= reach)
        )
        if candidates.size and label not in across:
            best = candidates[np.argmax(lengths[tall[candidates]])]
            across[label] = GridLine(label, "x", float(tall_x[best]))
            continue
        candidates = np.flatnonzero(
            (np.abs(wide_y - cy) <= radius * 0.3)
            & (np.minimum(np.abs(wide_x0 - cx), np.abs(wide_x1 - cx)) <= reach)
        )
        if candidates.size and label not in up:
            best = candidates[np.argmax(lengths[wide[candidates]])]
            up[label] = GridLine(label, "y", float(wide_y[best]))

    if len(across) < 2 or len(up) < 2:
        return _dashed(lines, bubbles)
    return GridSystem(tuple(_sorted(tuple(across.values()))), tuple(_sorted(tuple(up.values()))))


# --- Dashed and skewed gridlines -------------------------------------------------------------

# A dash is part of the line through a bubble when the line it lies on passes this close to
# the bubble's centre: a fixed allowance, and a little more for each millimetre away, since a
# short dash a metre off says where its line goes only so well.
DASH_OFF_MM = 0.3
DASH_AIM_DEGREES = 0.3
# Dashes shorter than this are the dots of a chain line, and hatching.
DASH_MIN_MM = 1.0
# The dashes of a gridline run within this angle of it, cover at least this share of the
# length they span, and begin within this many bubble radii of the bubble.
DASH_TURN_DEGREES = 0.5
DASH_COVER = 0.3
DASH_BEGINS_RADII = 10.0
# How many of the ways the dashes aimed at a bubble run are tried for its gridline.
DASH_WAYS_TRIED = 8
# Lines within this angle of each other run the same way; the two ways of one grid are
# square to each other within this.
SAME_WAY_DEGREES = 10.0
SQUARE_WITHIN_DEGREES = 5.0


@dataclass(frozen=True)
class _Drawn:
    """A gridline as found on the sheet: through its bubble's centre, at an angle."""

    label: str
    cx: float
    cy: float
    angle: float  # degrees from the x axis, from -45 to 135: about 90 is a line up the sheet
    length: float  # the length of line drawn along it
    start: float  # how far along the line from the bubble its dashes begin and end
    end: float


def _dashed(lines: Any, bubbles: list[tuple[str, float, float, float]]) -> GridSystem | None:
    """The grid of a sheet whose gridlines are dashed, or skewed, or both.

    Found on a real tender: every gridline is a chain line, so none is one long line, and a
    wing of the building stands at an angle to the sheet with gridlines of its own. A
    gridline is the dashes that lie on one line through its bubble's centre, whatever the
    angle. The lines that run one way make a family, and a grid is two families square to
    each other: the pair with the most lines.

    Only a bubble like those that stand in a row is a grid bubble: a sprinkler is a letter
    in a circle too, and a pipe runs through it.
    """
    # The bubbles that stand in rows, by label and place. By label alone a grid's "S" would
    # bring in every sprinkler on the sheet, and the size taken would be a sprinkler's.
    stood_across = {
        (str(label), float(str(at)))
        for row in _in_rows([(label, cx, cy, radius) for label, cx, cy, radius in bubbles])
        for label, at in row
    }
    stood_up = {
        (str(label), float(str(at)))
        for row in _in_rows([(label, cy, cx, radius) for label, cx, cy, radius in bubbles])
        for label, at in row
    }
    sizes = [
        radius
        for label, cx, cy, radius in bubbles
        if (label, round(cx, 3)) in stood_across or (label, round(cy, 3)) in stood_up
    ]
    size = float(np.median(sizes)) if sizes else _commonest_size(bubbles)
    if size is None:
        return None
    long_enough = np.flatnonzero(lines.lengths >= DASH_MIN_MM)
    if long_enough.size == 0:
        return None
    x0, y0 = lines.x0[long_enough], lines.y0[long_enough]
    x1, y1 = lines.x1[long_enough], lines.y1[long_enough]
    lengths = lines.lengths[long_enough]
    dashes = _Dashes(
        (x0 + x1) / 2,
        (y0 + y1) / 2,
        (x1 - x0) / lengths,
        (y1 - y0) / lengths,
        lengths,
        np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180,
    )

    drawn: dict[str, list[_Drawn]] = {}
    for label, cx, cy, radius in sorted(set(bubbles)):
        if not math.isclose(radius, size, rel_tol=0.1):
            continue
        found = _through(dashes, label, cx, cy, radius)
        if found is not None:
            drawn.setdefault(label, []).append(found)
    # A gridline has a bubble at each end, which is one line; one label on two lines is two
    # things, and neither is trusted.
    single = [
        max(found, key=lambda line: line.length)
        for found in drawn.values()
        if all(_same_line(found[0], other) for other in found[1:])
    ]

    families = _families(single)
    pairs = sorted(
        (
            (len(first) + len(second), index, other)
            for index, first in enumerate(families)
            for other, second in enumerate(families)
            if index < other
            and abs(abs(_mean_angle(first) - _mean_angle(second)) - 90) <= SQUARE_WITHIN_DEGREES
        ),
        key=lambda pair: (-pair[0], pair[1], pair[2]),
    )
    if not pairs:
        return None
    _, first, second = pairs[0]
    main = _grid_of(families[first], families[second])
    # A second grid of the families the first left: the wing's, beside the main block's.
    for _, third, fourth in pairs[1:]:
        if not {third, fourth} & {first, second}:
            return GridSystem(main.across, main.up, _grid_of(families[third], families[fourth]))
    return main


def _grid_of(first: list[_Drawn], second: list[_Drawn]) -> GridSystem:
    """Two families square to each other as a grid: the one nearer upright is "across"."""
    if abs(_mean_angle(first) - 90) > abs(_mean_angle(second) - 90):
        first, second = second, first
    across = tuple(_sorted(tuple(_grid_line(line, "x") for line in first)))
    up = tuple(_sorted(tuple(_grid_line(line, "y") for line in second)))
    return GridSystem(across, up)


def _commonest_size(bubbles: list[tuple[str, float, float, float]]) -> float | None:
    """The size of a grid bubble where none stand in a clean row: the size at which the
    most labels appear once each.

    Found on a real plan: the bubbles of two grids along one edge of the sheet, a wing's
    among the main block's, make one row whose labels are in no order. A gridline's label
    is on the sheet once (twice, at most, for a bubble at each end); a symbol's is on it
    wherever the symbol is drawn.
    """
    best: tuple[int, float] | None = None
    for radius in sorted({bubble[3] for bubble in bubbles}):
        labels = [
            label for label, _, _, other in bubbles if math.isclose(other, radius, rel_tol=0.1)
        ]
        once = sum(1 for label in set(labels) if labels.count(label) <= 2)
        if once >= 2 * MARKS_AT_LEAST and (best is None or once > best[0]):
            best = (once, radius)
    return None if best is None else best[1]


@dataclass(frozen=True)
class _Dashes:
    """Every segment long enough to be a dash: its middle, its direction, its length."""

    mid_x: Any
    mid_y: Any
    ux: Any
    uy: Any
    lengths: Any
    angles: Any  # degrees from the x axis, 0 to 180


def _through(dashes: _Dashes, label: str, cx: float, cy: float, radius: float) -> _Drawn | None:
    """The line drawn through a bubble's centre: the way most of the dashes aimed at it run.

    A dash is aimed at the bubble when the line it lies on passes through the centre. Their
    own directions say the angle, the long ones best. The line must then be drawn from the
    bubble: a gridline of another bubble that happens to point this way starts far off.
    """
    vx, vy = dashes.mid_x - cx, dashes.mid_y - cy
    away = np.hypot(vx, vy)
    off = np.abs(vx * dashes.uy - vy * dashes.ux)
    aimed = np.flatnonzero(
        (away > 1.2 * radius)
        & (off <= DASH_OFF_MM + away * math.sin(math.radians(DASH_AIM_DEGREES)))
    )
    if aimed.size == 0:
        return None
    vx, vy, weight, turn = vx[aimed], vy[aimed], dashes.lengths[aimed], dashes.angles[aimed]
    # Half-degree bins, each counted with the next so a line on a bin's edge is not split.
    bins = np.bincount((turn * 2).astype(int) % 360, weights=weight, minlength=360)
    paired = bins + np.roll(bins, -1)
    # The way most of them run is often not the gridline's: far across the sheet a great
    # deal of the building is aimed at any point. So the likeliest few ways are each tried.
    best: _Drawn | None = None
    for peak in np.argsort(paired)[::-1][:DASH_WAYS_TRIED]:
        found = _along(vx, vy, weight, turn, (int(peak) + 1) / 2, label, cx, cy, radius)
        if found is not None and (best is None or found.length > best.length):
            best = found
    return best


def _along(
    vx: Any,
    vy: Any,
    weight: Any,
    turn: Any,
    way: float,
    label: str,
    cx: float,
    cy: float,
    radius: float,
) -> _Drawn | None:
    """The gridline from a bubble that runs about this way, if one is drawn."""
    near = _turned(turn, way) <= DASH_TURN_DEGREES
    if not near.any():
        return None
    spread = np.radians(turn[near] * 2)
    heavy = weight[near] ** 2  # a long dash says its direction better, and counts for more
    angle = (
        math.degrees(
            math.atan2(float((heavy * np.sin(spread)).sum()), float((heavy * np.cos(spread)).sum()))
        )
        / 2
        % 180
    )
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    on = (np.abs(-sin * vx + cos * vy) <= DASH_OFF_MM) & (_turned(turn, angle) <= DASH_TURN_DEGREES)
    if not on.any():
        return None
    reach = cos * vx[on] + sin * vy[on]
    half = weight[on] / 2
    start, end = float((reach - half).min()), float((reach + half).max())
    total = float(weight[on].sum())
    begins = float(np.abs(reach).min())
    if (
        total < LINE_MIN_MM
        or total < DASH_COVER * (end - start)
        or begins > DASH_BEGINS_RADII * radius
    ):
        return None
    if angle > 135:
        # The same line, said the other way round: so its ends are the other way round too.
        angle, start, end = angle - 180, -end, -start
    return _Drawn(label, cx, cy, angle, total, start, end)


def _turned(angles: Any, angle: float) -> Any:
    """The angle between directions, which come round again at 180 degrees."""
    apart = np.abs(angles - angle) % 180
    return np.minimum(apart, 180 - apart)


def _same_line(first: _Drawn, second: _Drawn) -> bool:
    """Two bubbles on one gridline: the same angle, and each on the other's line."""
    turned = abs(first.angle - second.angle)
    if min(turned, 180 - turned) > 1.0:
        return False
    cos, sin = math.cos(math.radians(first.angle)), math.sin(math.radians(first.angle))
    return abs(-sin * (second.cx - first.cx) + cos * (second.cy - first.cy)) <= 1.0


def _mean_angle(family: list[_Drawn]) -> float:
    return sum(line.angle for line in family) / len(family)


def _families(found: list[_Drawn]) -> list[list[_Drawn]]:
    """The lines that run one way and are labelled one way (letters, or numbers), where
    there are at least two and their labels follow their order across the sheet."""
    families: list[list[_Drawn]] = []
    for labelled in sorted({kind(line.label) for line in found}):
        alike = sorted(
            (line for line in found if kind(line.label) == labelled),
            key=lambda line: line.angle,
        )
        groups: list[list[_Drawn]] = []
        for line in alike:
            if groups and line.angle - groups[-1][-1].angle <= SAME_WAY_DEGREES:
                groups[-1].append(line)
            else:
                groups.append([line])
        families.extend(group for group in groups if len(group) >= 2 and _in_order(group))
    return families


def _in_order(family: list[_Drawn]) -> bool:
    """Whether the labels rise, or fall, across the family: measured square to its lines,
    where the bubbles stand."""
    turn = math.radians(_mean_angle(family))
    placed = sorted(family, key=lambda line: -math.sin(turn) * line.cx + math.cos(turn) * line.cy)
    steps = [b - a for a, b in pairwise(ordinal(line.label) for line in placed)]
    return all(step > 0 for step in steps) or all(step < 0 for step in steps)


def _grid_line(line: _Drawn, axis: str) -> GridLine:
    """A found line as the grid keeps it: where it is at its bubble, how it leans, and how
    far along the other axis it is drawn."""
    cos, sin = math.cos(math.radians(line.angle)), math.sin(math.radians(line.angle))
    if axis == "x":
        position, origin, slope, run = line.cx, line.cy, cos / sin, sin
    else:
        position, origin, slope, run = line.cy, line.cx, sin / cos, cos
    if abs(slope) < math.tan(math.radians(0.1)):
        slope = 0.0  # square to the sheet
    ends = sorted((origin + run * line.start, origin + run * line.end))
    return GridLine(
        line.label,
        axis,
        round(position, 3),
        round(slope, 6),
        round(origin, 3),
        round(ends[0], 3),
        round(ends[1], 3),
    )


# Bubbles in one row are a grid's when there are at least this many of them.
MARKS_AT_LEAST = 3

# Rows of marks across the sheet and up it; a row is (label, position) in position order.
Marks = dict[str, list[list[list[object]]]]


def marks(table: pa.Table, bubbles: list[tuple[str, float, float, float]] | None = None) -> Marks:
    """Gridlines by their bubbles alone: rows of (label, x) `across` and of (label, y) `up`.

    A real plan draws its gridlines dashed, so no long line ends at a bubble and `detect`
    finds no grid. The bubbles still stand in a row along the sheet's edge, each on its own
    gridline, and that is enough to say how far apart two named gridlines are on paper:
    which is how one sheet's scale is checked against another's (FR-VIS-05).

    Each row is kept apart. A building with a skewed wing has a second grid whose bubbles
    stand in rows of their own, and a spacing is only ever taken along one row.
    """
    found = sorted(set(_bubbles(table) if bubbles is None else bubbles))
    return {
        "across": _in_rows([(label, cx, cy, radius) for label, cx, cy, radius in found]),
        "up": _in_rows([(label, cy, cx, radius) for label, cx, cy, radius in found]),
    }


def marks_in(found: Marks, extent: tuple[float, float, float, float]) -> Marks:
    """The marks whose gridline crosses a view."""

    def inside(rows: list[list[list[object]]], low: float, high: float) -> list[list[list[object]]]:
        kept = [[mark for mark in row if low <= float(str(mark[1])) <= high] for row in rows]
        return [row for row in kept if len(row) >= 2]

    return {
        "across": inside(found.get("across", []), extent[0], extent[2]),
        "up": inside(found.get("up", []), extent[1], extent[3]),
    }


def _in_rows(bubbles: list[tuple[str, float, float, float]]) -> list[list[list[object]]]:
    """The bubbles that stand in rows, a row at a time. Each bubble is its label, its
    position along the row, the coordinate a row shares, and its radius.

    A row is bubbles of one size on one line, each label once, all letters or all numbers,
    in their order. A symbol drawn as a letter in a circle is none of that: it repeats.
    """
    lines: list[list[tuple[str, float, float, float]]] = []
    for bubble in sorted(bubbles, key=lambda item: item[2]):
        if lines and abs(bubble[2] - lines[-1][-1][2]) <= 0.3 * bubble[3]:
            lines[-1].append(bubble)
        else:
            lines.append([bubble])
    found: dict[str, float] = {}
    twice: set[str] = set()
    rows: list[list[tuple[str, float]]] = []
    for line in lines:
        size = float(np.median([bubble[3] for bubble in line]))
        # Letters run one way across a building and numbers the other, so a row is of one
        # kind of label: a bubble of another that happens to stand on its line is not part
        # of it.
        for labelled in sorted({kind(bubble[0]) for bubble in line}):
            ordered = sorted(
                (
                    bubble
                    for bubble in line
                    if math.isclose(bubble[3], size, rel_tol=0.1) and kind(bubble[0]) == labelled
                ),
                key=lambda item: item[1],
            )
            labels = [bubble[0] for bubble in ordered]
            steps = [b - a for a, b in pairwise(ordinal(label) for label in labels)]
            if (
                len(ordered) < MARKS_AT_LEAST
                or len(set(labels)) != len(labels)
                or not (all(step >= 0 for step in steps) or all(step <= 0 for step in steps))
            ):
                continue
            row = []
            for label, position, _, radius in ordered:
                if label not in found:
                    found[label] = position
                    row.append((label, position))
                elif abs(found[label] - position) > 0.3 * radius:
                    # A gridline has a bubble at each end, which is one mark; one label at
                    # two places is two things, and marks neither.
                    twice.add(label)
            rows.append(row)
    kept = [[[label, round(at, 3)] for label, at in row if label not in twice] for row in rows]
    return [row for row in kept if len(row) >= 2]


def bubbles_of(table: pa.Table) -> list[tuple[str, float, float, float]]:
    """A sheet's grid bubbles, found once for `detect` and `marks`."""
    return _bubbles(table)


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

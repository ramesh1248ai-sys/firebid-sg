"""The spaces of a floor plan that need sprinklers, from its linework (P1-12).

A PDF floor plan has no rooms in it, only lines. The architect's base plan is drawn in greys
under the services in colour, so the spaces are what those greys leave:

1. **Walls.** The base plan's grey linework, less its structural grid (long dash-dot lines
   that would cut every room in two), drawn into a bitmap of the plan area.
2. **Footprint.** The building's outline: the walls thickened until even a wide facade
   opening closes, everything the outside cannot reach, then thinned back.
3. **Rooms.** The walls thickened by half a doorway, so a door's gap closes and a room is an
   enclosed region (a corridor, wider than a door, stays open). Each region inside the
   footprint is closed over its furniture and grown back to its walls, so its area is the
   room's and not the room less a margin at every wall.
4. **Zones.** What is left of the footprint that no room accounts for: spaces so full of
   furniture that their free floor breaks into pieces (a ward's bed bays, each bed inside a
   curtain track), measured whole. Thin strips, the walls themselves, are left out.

Sizes are in real millimetres, through the view's verified scale, so the same settings hold
at 1:100 and 1:50. The result is approximate in the way an estimator's reading of a plan is:
a space's area to within its wall thickness, which is what a head count needs.

Pure: geometry in, spaces out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa

from firebid.drawings import geometry

Box = tuple[float, float, float, float]
Polygon = list[tuple[float, float]]

# max(r, g, b) - min(r, g, b) above this is a colour (the services), not a grey (the plan).
SATURATED = 40


@dataclass(frozen=True)
class Settings:
    """How spaces are found. Real millimetres, through the view's scale."""

    door_gap_mm: float = 1200.0  # doorways up to this wide are closed
    facade_gap_mm: float = 3000.0  # openings in the outline up to this wide are closed
    furniture_mm: float = 2400.0  # islands up to this size inside a room are part of it
    wall_strip_mm: float = 600.0  # leftovers thinner than this are walls, not zones
    min_room_m2: float = 1.5  # smaller enclosed spaces are wall cavities and ducts
    min_building_m2: float = 40.0  # smaller enclosed outlines are symbols, not buildings
    px_per_mm: float = 2.0  # bitmap pixels per sheet millimetre


@dataclass
class Space:
    """One space: where it is on the sheet, how big it really is, what it is called."""

    index: int
    kind: str  # room | zone
    area_m2: float
    box: Box  # sheet mm
    centre: tuple[float, float]  # sheet mm, a point inside the space
    labels: list[str] = field(default_factory=list)
    mask: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 0), bool))
    origin: tuple[float, float] = (0.0, 0.0)  # sheet mm of the mask's top-left pixel
    px_per_mm: float = 2.0
    angle_deg: float = 0.0  # the space's main direction, for the head grid

    @property
    def name(self) -> str:
        return " ".join(self.labels)

    def contains(self, x: float, y: float) -> bool:
        """Whether a sheet point is inside the space."""
        col = math.floor((x - self.origin[0]) * self.px_per_mm)
        row = math.floor((y - self.origin[1]) * self.px_per_mm)
        height, width = self.mask.shape
        return 0 <= row < height and 0 <= col < width and bool(self.mask[row, col])


@dataclass
class Found:
    spaces: list[Space]
    base_colours: list[int]
    footprint_m2: float = 0.0
    # Boxes drawn with an X across them: lift shafts, risers and voids, never sprinklered.
    shafts: list[Polygon] = field(default_factory=list)
    note: str | None = None


# --- The base plan ----------------------------------------------------------------------------


def base_colours(table: pa.Table, region: Box) -> list[int]:
    """The architect's colours: the greys of the base plan.

    A base plan is usually drawn in more than one grey (partitions and furniture in one, the
    shell, cores and doors in another). Black is left out, as CAD exports draw text and the
    services' annotation in it; saturated colours are the services themselves. Greys with a
    sliver of the linework are noise.
    """
    seg = geometry.segments(table)
    if len(seg) == 0:
        return []
    colours = np.asarray(table.column("color").to_pylist(), dtype=np.int64)[seg.row]
    inside = _inside(seg, region)
    lengths = seg.lengths
    totals: dict[int, float] = {}
    for colour in np.unique(colours[inside]):
        if colour < 0 or not _is_grey(int(colour)) or colour in (0x000000, 0xFFFFFF):
            continue
        totals[int(colour)] = float(lengths[inside & (colours == colour)].sum())
    if not totals:
        return []
    largest = max(totals.values())
    return sorted(colour for colour, total in totals.items() if total >= 0.05 * largest)


# A grid line: dashes along one straight line across a good part of the plan.
GRID_SPAN = 0.3  # of the plan's larger side
GRID_MIN_GAPS = 8
GRID_GAP_MM = (0.5, 6.0)  # sheet mm: a dash pattern's gaps; a doorway is wider, a joint narrower
GRID_GAPS_PER_100MM = 2.5


def grid_lines(seg: geometry.Segments, wanted: np.ndarray, region: Box) -> np.ndarray:
    """Which of the wanted segments are dashes of a structural grid line.

    Segments are grouped by the infinite line they lie on (direction to half a degree,
    offset to half a millimetre). A group is a grid line when it reaches across
    `GRID_SPAN` of the plan with a dash pattern's gaps along it: many, short and regular.
    A wall can be as long and drawn in as many pieces, but its pieces meet end to end and
    are broken only at doorways, which are wider. Columns and walls drawn in the grid's
    colour are kept.
    """
    out = np.zeros(len(seg), dtype=bool)
    candidates = np.flatnonzero(wanted)
    if candidates.size == 0:
        return out
    # Binned twice, the second time by half a bin further, so a line whose pieces fall either
    # side of a bin's edge (a diagonal, through rounding) is still one line in one of them.
    for shift in (0.0, 0.5):
        out[candidates[_grid_pass(seg, candidates, region, shift)]] = True
    return out


def _grid_pass(
    seg: geometry.Segments, candidates: np.ndarray, region: Box, shift: float
) -> np.ndarray:
    x0, y0 = seg.x0[candidates], seg.y0[candidates]
    x1, y1 = seg.x1[candidates], seg.y1[candidates]
    angle = np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180.0
    angle_bin = np.floor(angle * 2 + shift)  # half a degree
    theta = np.radians((angle_bin + 0.5 - shift) / 2)
    offset = -x0 * np.sin(theta) + y0 * np.cos(theta)
    along0 = x0 * np.cos(theta) + y0 * np.sin(theta)
    along1 = x1 * np.cos(theta) + y1 * np.sin(theta)
    keys = angle_bin.astype(np.int64) * 100_000_000 + np.floor(offset * 2 + shift).astype(np.int64)
    lows, highs = np.minimum(along0, along1), np.maximum(along0, along1)
    order = np.lexsort((lows, keys))  # by line, then along it
    sorted_keys = keys[order]
    starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
    sizes = np.diff(np.r_[starts, sorted_keys.size])
    group = np.repeat(np.arange(starts.size), sizes)
    low, high = lows[order], highs[order]
    # The gap before each piece: its start less the furthest any earlier piece of its line
    # reached. One running maximum for every line, each lifted clear of the one before.
    lift = (float(np.abs(along0).max()) + float(np.abs(along1).max()) + 1.0) * 2
    reached = np.maximum.accumulate(high + group * lift) - group * lift
    gap = np.r_[0.0, low[1:] - reached[:-1]]
    gap[starts] = 0.0
    dash_gap = (gap >= GRID_GAP_MM[0]) & (gap <= GRID_GAP_MM[1])
    gaps = np.add.reduceat(dash_gap.astype(np.int64), starts)
    extent = np.maximum.reduceat(high, starts) - np.minimum.reduceat(low, starts)
    span = max(region[2] - region[0], region[3] - region[1])
    is_grid = (
        (extent > GRID_SPAN * span)
        & (gaps >= GRID_MIN_GAPS)
        & (gaps * 100.0 / np.maximum(extent, 1e-9) >= GRID_GAPS_PER_100MM)
    )
    picked: np.ndarray = order[np.repeat(is_grid, sizes)]
    return picked


# --- Finding the spaces ------------------------------------------------------------------------


def find(
    table: pa.Table,
    region: Box,
    denominator: float,
    *,
    colours: list[int] | None = None,
    exclude: list[Box] | None = None,
    scope: Polygon | None = None,
    settings: Settings | None = None,
) -> Found:
    """The spaces inside `region` (sheet mm), drawn at 1:`denominator`.

    `exclude` are boxes that are not plan (legends, notes); `scope` is the part of the plan
    this sheet is responsible for, when match lines share the floor between sheets.
    """
    settings = settings or Settings()
    chosen = colours if colours else base_colours(table, region)
    if not chosen:
        return Found([], [], note="no base plan linework was found in the plan area")
    scale = settings.px_per_mm
    x0, y0, x1, y1 = region
    shape = (max(1, math.ceil((y1 - y0) * scale)), max(1, math.ceil((x1 - x0) * scale)))

    seg = geometry.segments(table)
    seg_colours = np.asarray(table.column("color").to_pylist(), dtype=np.int64)[seg.row]
    base = np.isin(seg_colours, chosen) & _inside(seg, region)
    base &= ~grid_lines(seg, base, region)
    walls = _rasterise(seg, base, region, scale, shape)
    if not walls.any():
        return Found([], chosen, note="no base plan linework was found in the plan area")
    not_plan = np.zeros(shape, dtype=bool)
    for box in exclude or []:
        _fill_box(not_plan, box, region, scale)
    if scope:
        not_plan |= ~_polygon_mask(scope, region, scale, shape)

    px_mm = denominator / scale  # real millimetres per pixel
    px_m2 = (px_mm / 1000.0) ** 2

    def pixels(real_mm: float) -> int:
        return max(1, round(real_mm / px_mm))

    footprint = _footprint(
        walls, pixels(settings.facade_gap_mm / 2), settings.min_building_m2 / px_m2
    )
    found_shafts = shafts(seg, base, denominator)
    for shaft in found_shafts:
        not_plan |= _polygon_mask(shaft, region, scale, shape)
    footprint &= ~not_plan
    reach = pixels(settings.door_gap_mm / 2)
    blocked = _dilate(walls, reach) | ~footprint
    labels, count = _label(~blocked)
    texts = _space_texts(table, region)

    spaces: list[Space] = []
    claimed = np.zeros(shape, dtype=bool)
    furniture = pixels(settings.furniture_mm / 2)
    boxes = _boxes(labels, count)
    for index in range(1, count + 1):
        top, bottom, left, right = (int(v) for v in boxes[index])
        if bottom < top:
            continue
        margin = reach + furniture + 2
        r0, r1 = max(0, top - margin), min(shape[0], bottom + margin + 1)
        c0, c1 = max(0, left - margin), min(shape[1], right + margin + 1)
        crop = labels[r0:r1, c0:c1]
        own = crop == index
        others = (crop > 0) & ~own
        # Close over the furniture, then grow back to the walls: never into another room, and
        # never out of the building.
        closed = _erode(_dilate(own, furniture), furniture) | own
        mask = _dilate(closed, reach) & ~others & footprint[r0:r1, c0:c1]
        area = float(mask.sum()) * px_m2
        if area < settings.min_room_m2:
            continue
        claimed[r0:r1, c0:c1] |= mask
        spaces.append(_space(len(spaces), "room", mask, own, (r0, c0), region, scale, area))

    # Zones: the footprint no room claimed, less the thin strips that are walls.
    strip = pixels(settings.wall_strip_mm / 2)
    rest = footprint & ~claimed
    rest = _dilate(_erode(rest, strip), strip) & rest
    zone_labels, zone_count = _label(rest)
    zone_boxes = _boxes(zone_labels, zone_count)
    for index in range(1, zone_count + 1):
        top, bottom, left, right = (int(v) for v in zone_boxes[index])
        if bottom < top:
            continue
        mask = zone_labels[top : bottom + 1, left : right + 1] == index
        area = float(mask.sum()) * px_m2
        if area < settings.min_room_m2:
            continue
        spaces.append(_space(len(spaces), "zone", mask, mask, (top, left), region, scale, area))

    base_lines = _wall_lines(seg, base)
    for space in spaces:
        space.labels = [text for (tx, ty, text) in texts if space.contains(tx, ty)]
        space.angle_deg = _wall_direction(base_lines, space.box)
    return Found(
        spaces,
        chosen,
        footprint_m2=round(float(footprint.sum()) * px_m2, 1),
        shafts=found_shafts,
    )


def _space(
    index: int,
    kind: str,
    mask: np.ndarray,
    free: np.ndarray,
    corner: tuple[int, int],
    region: Box,
    scale: float,
    area: float,
) -> Space:
    origin = (region[0] + corner[1] / scale, region[1] + corner[0] / scale)
    ys, xs = np.nonzero(free)
    # A point inside the space itself, not just inside its box: the free pixel nearest the
    # centroid of its free floor.
    cy, cx = ys.mean(), xs.mean()
    nearest = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
    centre = (origin[0] + (xs[nearest] + 0.5) / scale, origin[1] + (ys[nearest] + 0.5) / scale)
    mr, mc = np.nonzero(mask)
    box = (
        origin[0] + mc.min() / scale,
        origin[1] + mr.min() / scale,
        origin[0] + (mc.max() + 1) / scale,
        origin[1] + (mr.max() + 1) / scale,
    )
    return Space(
        index=index,
        kind=kind,
        area_m2=round(area, 2),
        box=box,
        centre=centre,
        mask=mask,
        origin=origin,
        px_per_mm=scale,
    )


def _footprint(walls: np.ndarray, reach: int, min_pixels: float) -> np.ndarray:
    """The building: what the outside cannot reach once its wide openings are closed.

    Enclosed outlines smaller than a building (a grid bubble, a symbol drawn in the plan's
    grey) are not buildings.
    """
    closed = _dilate(walls, reach)
    labels, _ = _label(~closed)
    edge = np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    outside = np.isin(labels, edge[edge > 0])
    # Thin the outside back by what the walls were thickened by, so the outline is the wall's.
    building = ~_dilate(outside, reach)
    parts, count = _label(building)
    sizes = np.bincount(parts.ravel(), minlength=count + 1)
    big = np.flatnonzero(sizes >= min_pixels)
    result: np.ndarray = np.isin(parts, big[big > 0])
    return result


# A shaft's X: two diagonals crossing at their midpoints, of about the same length.
# Real: a diagonal's length, from a 1 m riser to a goods lift. A 600 mm ceiling diffuser drawn
# with an X (849 mm across) is below it.
SHAFT_MM = (1200.0, 6000.0)
SHAFT_LENGTH_RATIO = 0.08
SHAFT_MIN_ANGLE = 25.0


def shafts(seg: geometry.Segments, wanted: np.ndarray, denominator: float) -> list[Polygon]:
    """Lift shafts, risers and voids: boxes drawn with an X across them.

    The convention on Singapore architectural plans (and most others): a space with no floor
    at this level is crossed corner to corner. Two segments whose midpoints coincide, of
    about the same length and at a clear angle, are the X; the box is their four ends. It
    holds for a shaft at any rotation.
    """
    index = np.flatnonzero(wanted)
    if index.size == 0:
        return []
    x0, y0, x1, y1 = seg.x0[index], seg.y0[index], seg.x1[index], seg.y1[index]
    lengths = np.hypot(x1 - x0, y1 - y0)
    real = lengths * denominator
    keep = (real >= SHAFT_MM[0]) & (real <= SHAFT_MM[1])
    index, x0, y0, x1, y1, lengths = (
        index[keep],
        x0[keep],
        y0[keep],
        x1[keep],
        y1[keep],
        lengths[keep],
    )
    mx, my = np.round((x0 + x1) / 2 * 2), np.round((y0 + y1) / 2 * 2)  # half-mm midpoints
    angle = np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180.0
    by_middle: dict[tuple[int, int], list[int]] = {}
    middles = zip(mx.astype(np.int64).tolist(), my.astype(np.int64).tolist(), strict=True)
    for i, key in enumerate(middles):
        by_middle.setdefault(key, []).append(i)
    found: list[Polygon] = []
    seen: set[tuple[int, int]] = set()
    for key, members in by_middle.items():
        if len(members) < 2 or key in seen:
            continue
        done = False
        for a_pos, a in enumerate(members):
            for b in members[a_pos + 1 :]:
                turn = abs(angle[a] - angle[b])
                turn = min(turn, 180.0 - turn)
                similar = abs(lengths[a] - lengths[b]) <= SHAFT_LENGTH_RATIO * lengths[a]
                if turn >= SHAFT_MIN_ANGLE and similar:
                    found.append(
                        [
                            (float(x0[a]), float(y0[a])),
                            (float(x0[b]), float(y0[b])),
                            (float(x1[a]), float(y1[a])),
                            (float(x1[b]), float(y1[b])),
                        ]
                    )
                    seen.add(key)
                    done = True
                    break
            if done:
                break
    return found


# --- Labels -----------------------------------------------------------------------------------

# Text on a fire protection plan that is not a space's name: pipe sizes and tags, grid
# references, dimensions, levels, notes and cross-references.
NOT_A_NAME = re.compile(
    r"Ø|\bmm\b|\bDN\s?\d|^\d+([.,]\d+)?$|^[A-Z]{1,2}\d{0,2}$|\bF/[AB]\b|\bT/[AB]\b|\(TYP\.?\)"
    r"|^(SPR|HR|WR|SPK|FH|FS|FM|FI|CM|SAP|S|PA)$|FFL|\+\d|REFER TO|CONTINUATION|\d{6,}_",
    re.IGNORECASE,
)


def _space_texts(table: pa.Table, region: Box) -> list[tuple[float, float, str]]:
    found = []
    for span in geometry.texts(table):
        text = str(span.get("text") or "").strip()
        if len(text) < 2 or NOT_A_NAME.search(text) or not re.search(r"[A-Za-z]{2}", text):
            continue
        x = (float(span["minx"]) + float(span["maxx"])) / 2
        y = (float(span["miny"]) + float(span["maxy"])) / 2
        if region[0] <= x <= region[2] and region[1] <= y <= region[3]:
            found.append((x, y, text))
    return found


# --- Bitmaps ----------------------------------------------------------------------------------


def _is_grey(colour: int) -> bool:
    r, g, b = (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF
    return max(r, g, b) - min(r, g, b) <= SATURATED


def _inside(seg: geometry.Segments, region: Box) -> np.ndarray:
    mx, my = (seg.x0 + seg.x1) / 2, (seg.y0 + seg.y1) / 2
    result: np.ndarray = (
        (mx >= region[0]) & (mx <= region[2]) & (my >= region[1]) & (my <= region[3])
    )
    return result


def _rasterise(
    seg: geometry.Segments,
    wanted: np.ndarray,
    region: Box,
    scale: float,
    shape: tuple[int, int],
) -> np.ndarray:
    """The wanted segments as pixels: points sampled along each, half a pixel apart."""
    out = np.zeros(shape, dtype=bool)
    if not wanted.any():
        return out
    x0 = (seg.x0[wanted] - region[0]) * scale
    y0 = (seg.y0[wanted] - region[1]) * scale
    x1 = (seg.x1[wanted] - region[0]) * scale
    y1 = (seg.y1[wanted] - region[1]) * scale
    steps = np.ceil(np.hypot(x1 - x0, y1 - y0) * 2).astype(np.int64) + 1
    owner = np.repeat(np.arange(steps.size), steps)
    first = np.repeat(np.cumsum(steps) - steps, steps)
    t = (np.arange(owner.size) - first) / np.maximum(steps[owner] - 1, 1)
    xs = np.floor(x0[owner] + t * (x1 - x0)[owner]).astype(np.int64)
    ys = np.floor(y0[owner] + t * (y1 - y0)[owner]).astype(np.int64)
    ok = (xs >= 0) & (xs < shape[1]) & (ys >= 0) & (ys < shape[0])
    out[ys[ok], xs[ok]] = True
    return out


def _polygon_mask(
    polygon: Polygon, region: Box, scale: float, shape: tuple[int, int]
) -> np.ndarray:
    from PIL import Image, ImageDraw

    image = Image.new("1", (shape[1], shape[0]), 0)
    points = [((x - region[0]) * scale, (y - region[1]) * scale) for x, y in polygon]
    ImageDraw.Draw(image).polygon(points, fill=1)
    return np.asarray(image, dtype=bool)


def _box_any(mask: np.ndarray, radius: int) -> np.ndarray:
    """True where any pixel within a square of `radius` is True (a square dilation)."""
    if radius <= 0:
        return mask.copy()
    size = 2 * radius + 1
    padded = np.pad(mask.astype(np.int32), radius)
    # Separable: a running sum along each axis, then any() as sum > 0.
    rows = np.cumsum(padded, axis=0)
    rows = np.concatenate([rows[:size], rows[size:] - rows[:-size]])[2 * radius :]
    cols = np.cumsum(rows, axis=1)
    cols = np.concatenate([cols[:, :size], cols[:, size:] - cols[:, :-size]], axis=1)
    result: np.ndarray = cols[:, 2 * radius :] > 0
    return result


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    return _box_any(mask, radius)


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    return ~_box_any(~mask, radius)


def _fill_box(mask: np.ndarray, box: Box, region: Box, scale: float) -> None:
    c0 = max(0, int((box[0] - region[0]) * scale))
    r0 = max(0, int((box[1] - region[1]) * scale))
    c1 = min(mask.shape[1], math.ceil((box[2] - region[0]) * scale))
    r1 = min(mask.shape[0], math.ceil((box[3] - region[1]) * scale))
    if c1 > c0 and r1 > r0:
        mask[r0:r1, c0:c1] = True


def _label(free: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected regions of `free`, labelled 1..n (0 is not free).

    By runs: each row's free runs, joined to the overlapping runs of the row above through a
    union-find. A plan bitmap has a few hundred thousand runs, so this is fast in Python.
    """
    height, width = free.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = free
    change = np.diff(padded, axis=1)
    run_rows, run_starts = np.nonzero(change == 1)
    _, run_ends = np.nonzero(change == -1)  # exclusive; same order as the starts
    n = run_rows.size
    if n == 0:
        return np.zeros(free.shape, dtype=np.int32), 0
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    row_first = np.searchsorted(run_rows, np.arange(height + 1))
    starts, ends = run_starts.tolist(), run_ends.tolist()
    for row in range(1, height):
        a, a_end = int(row_first[row - 1]), int(row_first[row])
        b, b_end = int(row_first[row]), int(row_first[row + 1])
        while a < a_end and b < b_end:
            if starts[a] < ends[b] and starts[b] < ends[a]:
                ra, rb = root(a), root(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            if ends[a] < ends[b]:
                a += 1
            else:
                b += 1

    roots = np.fromiter((root(i) for i in range(n)), dtype=np.int64, count=n)
    unique, numbered = np.unique(roots, return_inverse=True)
    # Paint the runs: mark each run's start with its label and its end with minus it, then a
    # cumulative sum along the row fills the run between.
    paint = np.zeros((height, width + 1), dtype=np.int64)
    np.add.at(paint, (run_rows, run_starts), numbered + 1)
    np.add.at(paint, (run_rows, run_ends), -(numbered + 1))
    labels = np.cumsum(paint, axis=1)[:, :width].astype(np.int32)
    return labels, int(unique.size)


def _boxes(labels: np.ndarray, count: int) -> np.ndarray:
    """Each label's (top, bottom, left, right) in pixels, in one pass. Empty: bottom < top."""
    boxes = np.zeros((count + 1, 4), dtype=np.int64)
    boxes[:, 0] = labels.shape[0]
    boxes[:, 1] = -1
    boxes[:, 2] = labels.shape[1]
    boxes[:, 3] = -1
    rows, cols = np.nonzero(labels)
    owner = labels[rows, cols]
    np.minimum.at(boxes[:, 0], owner, rows)
    np.maximum.at(boxes[:, 1], owner, rows)
    np.minimum.at(boxes[:, 2], owner, cols)
    np.maximum.at(boxes[:, 3], owner, cols)
    return boxes


WallLines = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def _wall_lines(seg: geometry.Segments, wanted: np.ndarray) -> WallLines:
    """The base plan's lines as `_wall_direction` reads them: each one's midpoint, the degree
    its direction falls in modulo a right angle, and its length. Worked out once a sheet:
    done again for every space it was half of finding a floor's rooms.
    """
    x0, y0, x1, y1 = seg.x0[wanted], seg.y0[wanted], seg.x1[wanted], seg.y1[wanted]
    dx, dy = x1 - x0, y1 - y0
    angles = np.degrees(np.arctan2(dy, dx)) % 90.0
    degree = np.round(angles).astype(np.int64) % 90
    return (x0 + x1) / 2, (y0 + y1) / 2, degree, np.hypot(dx, dy)


def _wall_direction(walls: WallLines, box: Box) -> float:
    """The direction a space's walls run in, in degrees within (-45, 45] (sheet frame).

    The head grid is set out square to the walls, so a wing built at an angle gets a grid at
    that angle. From the base plan's lines in and around the space: the direction, taken
    modulo a right angle, that the greatest length of them shares. Within a degree of the
    sheet's own axes it is the sheet's.
    """
    margin = 2.0
    mx, my, degree, lengths = walls
    near = (
        (mx >= box[0] - margin)
        & (mx <= box[2] + margin)
        & (my >= box[1] - margin)
        & (my <= box[3] + margin)
    )
    if not near.any():
        return 0.0
    totals = np.bincount(degree[near], lengths[near], 90)
    # A wall's pieces fall in neighbouring degrees: count each degree with its neighbours.
    smoothed = totals + np.roll(totals, 1) + np.roll(totals, -1)
    peak = int(np.argmax(smoothed))
    if smoothed[peak] <= smoothed[0] * 1.2:
        peak = 0  # no clearer direction than the sheet's own
    return float(peak if peak <= 45 else peak - 90)


def summary(found: Found) -> dict[str, Any]:
    return {
        "rooms": sum(1 for s in found.spaces if s.kind == "room"),
        "zones": sum(1 for s in found.spaces if s.kind == "zone"),
        "area_m2": round(sum(s.area_m2 for s in found.spaces), 1),
        "footprint_m2": found.footprint_m2,
        "base_colours": [f"#{colour:06x}" for colour in found.base_colours],
        "note": found.note,
    }

"""A proposed sprinkler layout for the spaces of a plan (P1-12).

For each space that needs sprinklers:

* **Heads** on a rectangular grid along the space's main direction, at the design spacing
  (the company's preferred grid, never wider than the design criterion allows), centred so
  each wall is at most half a spacing from the nearest head. A grid point is kept when it
  falls on the space's floor; every space keeps at least one head.
* **Range pipes** along each row of heads, sized by the number of heads each length feeds
  (the pre-calculated range-pipe table of the design rules), plus the **feed**: a length
  from the row's end to the nearest pipe the tender drew. A row with no drawn pipe within
  reach gets the remote-feed allowance instead, and says so.
* **Head type and rating** by rules on the space's name and the level (plant rooms at a
  higher rating; car parks upright), each recorded with the rule that set it.

Spaces the rules omit (stairs, shafts, risers, voids) get nothing, and are listed with the
rule that omitted them, so a person sees what was left out and why (FC07: omissions are
stated, never assumed).

Arithmetic is in integer millimetres (guardrail 3). Pure: spaces and rules in, layout out.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from firebid.design.rooms import Space


@dataclass(frozen=True)
class Criterion:
    """A design criterion: how far apart heads may be, and how much floor each may cover."""

    key: str
    title: str
    max_area_m2: float
    max_spacing_mm: tuple[int, int]
    source: str  # "sheet note FP-..., 'MAXIMUM SPACING: (4M X 3M)'" or "design rules default"
    k_factor: str | None = None
    response: str | None = None


@dataclass(frozen=True)
class HeadRule:
    """What kind of head a space gets, when its name or level matches."""

    when_name: str | None  # a regular expression on the space's name
    when_level: str | None  # a regular expression on the level
    object_type: str  # sprinkler_pendent | sprinkler_upright | ...
    temperature_c: int
    note: str


@dataclass(frozen=True)
class Rules:
    """The design rules: a versioned, confirmed set (FR-ADM-02's pattern)."""

    version: int
    status: str
    grid_mm: tuple[int, int]
    grid_from_m2: float  # spaces at least this large follow the preferred grid
    omit_names: tuple[str, ...]  # regular expressions
    head_rules: tuple[HeadRule, ...]
    default_head: HeadRule
    range_limits: tuple[tuple[int, int], ...]  # (DN, most heads it may feed), smallest first
    feed_reach_mm: int  # a drawn pipe further than this is too far to feed a row
    remote_feed_mm_per_head: int
    remote_feed_dn: int

    def omitted_by(self, names: list[str]) -> str | None:
        """The omission rule a space's names match, if any.

        A space read with several names (a zone spanning a corridor and a riser) is omitted
        only when every name is one the rules omit: one riser does not empty a ward. A space
        with no name is never omitted: it is sprinklered, as the Fire Code's default is.
        """
        matched = []
        for name in names:
            hit = next((p for p in self.omit_names if re.search(p, name, re.IGNORECASE)), None)
            if hit is None:
                return None
            matched.append(hit)
        return matched[0] if matched else None

    def head_for(self, names: list[str], level: str | None) -> HeadRule:
        """The first rule the space matches, or the default.

        A rule on the name holds when every name of the space matches it, as an omission
        does: one pump room in a zone read with ten names does not re-rate the zone.
        """
        for rule in self.head_rules:
            if rule.when_name and not (
                names and all(re.search(rule.when_name, n, re.IGNORECASE) for n in names)
            ):
                continue
            if rule.when_level and not re.search(rule.when_level, level or "", re.IGNORECASE):
                continue
            if rule.when_name or rule.when_level:
                return rule
        return self.default_head

    def range_dn(self, heads_fed: int) -> int:
        for dn, most in self.range_limits:
            if heads_fed <= most:
                return dn
        return self.range_limits[-1][0]


@dataclass
class Head:
    x: float  # sheet mm
    y: float
    space: int
    object_type: str
    temperature_c: int
    rule_note: str


@dataclass
class RangePipe:
    """One row's pipe, as lengths by size, with the points it runs through (sheet mm)."""

    space: int
    points: list[tuple[float, float]]
    lengths_mm: dict[int, int]  # DN -> length along the row
    feed_mm: int  # from the row to the drawn pipe
    feed_dn: int
    remote: bool  # no drawn pipe within reach: an allowance, not a measured feed
    heads: int
    # Each length between two heads, from the feed end: its size and its length.
    pieces: list[tuple[int, int]] = field(default_factory=list)
    feed_to: tuple[float, float] | None = None  # where the feed meets the drawn pipe

    def stretches(self) -> list[tuple[int, int, list[tuple[float, float]]]]:
        """The row as stretches of one size: (DN, length in mm, the points it runs through)."""
        out: list[tuple[int, int, list[tuple[float, float]]]] = []
        for index, (dn, length) in enumerate(self.pieces):
            ends = [self.points[index], self.points[index + 1]]
            if out and out[-1][0] == dn:
                out[-1] = (dn, out[-1][1] + length, [*out[-1][2], ends[1]])
            else:
                out.append((dn, length, ends))
        return out


@dataclass
class SpaceResult:
    space: int
    kind: str
    name: str
    area_m2: float
    criterion: str
    heads: int
    pitch_mm: tuple[int, int]
    omitted_by: str | None = None
    note: str | None = None


@dataclass
class Layout:
    heads: list[Head] = field(default_factory=list)
    ranges: list[RangePipe] = field(default_factory=list)
    spaces: list[SpaceResult] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        by_dn: dict[int, int] = {}
        for pipe in self.ranges:
            for dn, length in pipe.lengths_mm.items():
                by_dn[dn] = by_dn.get(dn, 0) + length
            if pipe.feed_mm:
                by_dn[pipe.feed_dn] = by_dn.get(pipe.feed_dn, 0) + pipe.feed_mm
        return {
            "heads": len(self.heads),
            "range_pipe_m": {dn: round(mm / 1000, 1) for dn, mm in sorted(by_dn.items())},
            "spaces": len(self.spaces),
            "omitted": sum(1 for s in self.spaces if s.omitted_by),
            "remote_rows": sum(1 for p in self.ranges if p.remote),
        }


def pitch(criterion: Criterion, rules: Rules, area_m2: float | None = None) -> tuple[int, int]:
    """A space's head spacing, within the criterion's spacing and area.

    An open floor follows the preferred grid, set out on the structure; the stricter of it
    and the criterion governs (the tender's Note 3: the more stringent applies). A room
    smaller than `grid_from_m2` is set out on its own walls, with the fewest heads the
    criterion allows. With no area given, the open floor's spacing.
    """
    along, across = criterion.max_spacing_mm
    if area_m2 is None or area_m2 >= rules.grid_from_m2:
        along, across = min(rules.grid_mm[0], along), min(rules.grid_mm[1], across)
    limit_mm2 = criterion.max_area_m2 * 1_000_000
    while along * across > limit_mm2:
        # Shrink the longer side first, in 50 mm steps, until a head covers no more than allowed.
        if along >= across:
            along -= 50
        else:
            across -= 50
    return along, across


def plan(
    spaces: list[Space],
    denominator: float,
    criterion: Criterion,
    rules: Rules,
    *,
    level: str | None = None,
    pipes: list[list[tuple[float, float]]] | None = None,
) -> Layout:
    """Heads and range pipes for every space, with what was omitted and why.

    `pipes` are the drawn pipe runs' points (sheet mm) a row may be fed from.
    """
    layout = Layout()
    feeders = _feeders(pipes or [])
    for space in spaces:
        omitted = rules.omitted_by(space.labels)
        along, across = pitch(criterion, rules, space.area_m2)
        result = SpaceResult(
            space=space.index,
            kind=space.kind,
            name=space.name,
            area_m2=space.area_m2,
            criterion=criterion.key,
            heads=0,
            pitch_mm=(along, across),
            omitted_by=omitted,
        )
        layout.spaces.append(result)
        if omitted:
            continue
        head_rule = rules.head_for(space.labels, level)
        rows = _grid(space, denominator, along, across)
        if not rows:
            # Too small or too odd for the grid: one head, at the point inside it.
            rows = [[space.centre]]
            result.note = "one head: the space is smaller than one grid spacing"
        for row in rows:
            for x, y in row:
                layout.heads.append(
                    Head(
                        x,
                        y,
                        space.index,
                        head_rule.object_type,
                        head_rule.temperature_c,
                        head_rule.note,
                    )
                )
            layout.ranges.append(_range(space.index, row, denominator, rules, feeders))
        result.heads = sum(len(row) for row in rows)
    return layout


def _grid(
    space: Space, denominator: float, along: int, across: int
) -> list[list[tuple[float, float]]]:
    """Grid points on the space's floor, row by row, in sheet mm.

    In the space's own frame (u along its main direction, v across), from the extent of its
    floor: n = ceil(extent / spacing) heads each way at extent / n apart, the first half a
    spacing from the wall, so no wall is further than half a spacing from a head.
    """
    rows_px, cols_px = np.nonzero(space.mask)
    if rows_px.size == 0:
        return []
    scale = space.px_per_mm
    xs = space.origin[0] + (cols_px + 0.5) / scale
    ys = space.origin[1] + (rows_px + 0.5) / scale
    theta = math.radians(space.angle_deg)
    cos, sin = math.cos(theta), math.sin(theta)
    u = xs * cos + ys * sin
    v = -xs * sin + ys * cos
    # The floor reaches half a pixel beyond its outermost pixels' centres.
    edge = 0.5 / scale
    u0, u1 = float(u.min()) - edge, float(u.max()) + edge
    v0, v1 = float(v.min()) - edge, float(v.max()) + edge

    def count(extent: float, spacing: int) -> int:
        return max(1, math.ceil(extent * denominator / spacing - 1e-9))

    # The wider spacing runs whichever way needs fewer heads.
    nu, nv = min(
        (count(u1 - u0, along), count(v1 - v0, across)),
        (count(u1 - u0, across), count(v1 - v0, along)),
        key=lambda n: n[0] * n[1],
    )
    step_u, step_v = (u1 - u0) / nu, (v1 - v0) / nv
    rows: list[list[tuple[float, float]]] = []
    for j in range(nv):
        vv = v0 + step_v * (j + 0.5)
        row: list[tuple[float, float]] = []
        for i in range(nu):
            uu = u0 + step_u * (i + 0.5)
            x, y = uu * cos - vv * sin, uu * sin + vv * cos
            if not space.contains(x, y):
                # Off the floor: the row ends here, and a new one starts beyond the gap.
                if row:
                    rows.append(row)
                row = []
                continue
            if row and not space.contains((row[-1][0] + x) / 2, (row[-1][1] + y) / 2):
                # A wall between two heads: a range pipe does not run through it.
                rows.append(row)
                row = []
            row.append((x, y))
        if row:
            rows.append(row)
    return rows


@dataclass(frozen=True)
class _Feeders:
    starts: np.ndarray  # (n, 2) segment starts, sheet mm
    ends: np.ndarray


def _feeders(pipes: list[list[tuple[float, float]]]) -> _Feeders:
    starts, ends = [], []
    for points in pipes:
        for a, b in itertools.pairwise(points):
            starts.append(a)
            ends.append(b)
    return _Feeders(
        np.asarray(starts, dtype=float).reshape(-1, 2), np.asarray(ends, dtype=float).reshape(-1, 2)
    )


def _nearest(feeders: _Feeders, x: float, y: float) -> tuple[float, tuple[float, float]] | None:
    """The nearest drawn pipe to a point: how far (sheet mm) and where. None if none drawn."""
    if feeders.starts.size == 0:
        return None
    a, b = feeders.starts, feeders.ends
    ab = b - a
    t = np.clip(
        ((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1])
        / np.maximum((ab**2).sum(axis=1), 1e-12),
        0.0,
        1.0,
    )
    px, py = a[:, 0] + t * ab[:, 0], a[:, 1] + t * ab[:, 1]
    distance = np.sqrt((px - x) ** 2 + (py - y) ** 2)
    best = int(np.argmin(distance))
    return float(distance[best]), (float(px[best]), float(py[best]))


def _range(
    space: int,
    row: list[tuple[float, float]],
    denominator: float,
    rules: Rules,
    feeders: _Feeders,
) -> RangePipe:
    """A row's range pipe, fed from the end nearer a drawn pipe.

    Between heads, each length carries the heads beyond it, and is sized for them. The feed
    from the row to the drawn pipe carries the whole row.
    """
    found = [
        (near[0], index, near[1])
        for index, point in ((0, row[0]), (len(row) - 1, row[-1]))
        if (near := _nearest(feeders, point[0], point[1])) is not None
    ]
    nearest = min(found) if found else None
    ordered = row if nearest is None or nearest[1] == 0 else list(reversed(row))
    lengths: dict[int, int] = {}
    pieces: list[tuple[int, int]] = []
    for k in range(len(ordered) - 1):
        (x0, y0), (x1, y1) = ordered[k], ordered[k + 1]
        dn = rules.range_dn(len(ordered) - (k + 1))
        length = round(math.hypot(x1 - x0, y1 - y0) * denominator)
        lengths[dn] = lengths.get(dn, 0) + length
        pieces.append((dn, length))
    reach = nearest[0] * denominator if nearest is not None else None
    if nearest is None or reach is None or reach > rules.feed_reach_mm:
        return RangePipe(
            space=space,
            points=ordered,
            lengths_mm=lengths,
            feed_mm=rules.remote_feed_mm_per_head * len(row),
            feed_dn=rules.remote_feed_dn,
            remote=True,
            heads=len(row),
            pieces=pieces,
        )
    return RangePipe(
        space=space,
        points=ordered,
        lengths_mm=lengths,
        feed_mm=round(reach),
        feed_dn=rules.range_dn(len(row)),
        remote=False,
        heads=len(row),
        pieces=pieces,
        feed_to=nearest[2],
    )

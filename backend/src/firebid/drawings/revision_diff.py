"""What changed between two revisions of a sheet (FR-DOC-08).

Two revisions of one drawing are two sheets, each with its own detections and pipe runs.
Comparing them takes two steps:

1. **Align.** The older sheet's positions are brought onto the newer sheet. Three ways, tried
   in this order, and the first that brings at least half of the older elements onto a
   counterpart is used (the best of them if none does):

   * **grid:** the structural grid lines both sheets label alike give a scale and an offset
     along each axis;
   * **frame:** the two sheets' frames (the page, whose corner the title block sits in) are
     laid over each other;
   * **fit:** the offset most pairs of like symbols agree on, for sheets with no common grid
     whose frames do not help.

2. **Diff.** An element is **unchanged** when the same thing is at the same place with the
   same attributes; **changed** when it is at the same place with other attributes (a
   pendent head now an upright, a DN50 branch now DN65), or when a pipe run from the same
   point in the same direction is now a different length; **added** or **removed**
   otherwise. A symbol that was moved is a removal and an addition: the drawing does not
   say they are one thing.

Every change carries where it is on the newer sheet, so it can be drawn over it.

Pure: two sets of elements with their grids and frames in; an alignment and changes out.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Two marks this close on paper, once aligned, are at the same place.
SAME_PLACE_MM = 1.0
# How many of the older elements must find a counterpart for an alignment to be trusted.
ENOUGH = 0.5
# The fit rounds offsets to this before counting which one most pairs agree on.
FIT_STEP_MM = 0.5

Point = tuple[float, float]
Frame = tuple[float, float, float, float]  # x0, y0, x1, y1 in sheet mm
GridLines = dict[str, dict[str, float]]  # {"across": {label: x}, "up": {label: y}}


@dataclass(frozen=True)
class Element:
    """A detection or a pipe run of one revision, in its own sheet's millimetres."""

    id: str
    kind: str  # object | riser | drop | run
    object_type: str
    x: float
    y: float
    attributes: dict[str, Any] = field(default_factory=dict)
    points: tuple[Point, ...] = ()  # a run's points, in order
    length_mm: float | None = None

    @property
    def ends(self) -> tuple[Point, Point] | None:
        return (self.points[0], self.points[-1]) if len(self.points) >= 2 else None


@dataclass(frozen=True)
class Alignment:
    """How an older sheet's position becomes the newer sheet's: scale, then offset."""

    method: str  # grid | frame | fit | identity
    scale: Point = (1.0, 1.0)
    offset: Point = (0.0, 0.0)
    matched: float = 0.0  # the share of older elements it brings onto a counterpart

    def apply(self, point: Point) -> Point:
        return (
            point[0] * self.scale[0] + self.offset[0],
            point[1] * self.scale[1] + self.offset[1],
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "scale": [round(v, 6) for v in self.scale],
            "offset_mm": [round(v, 3) for v in self.offset],
            "matched": round(self.matched, 3),
        }


@dataclass(frozen=True)
class Change:
    change: str  # added | removed | changed
    kind: str
    object_type: str
    x: float  # on the newer sheet
    y: float
    old_id: str | None = None
    new_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    points: tuple[Point, ...] = ()  # on the newer sheet

    def as_json(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "kind": self.kind,
            "object_type": self.object_type,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "old_id": self.old_id,
            "new_id": self.new_id,
            "before": self.before,
            "after": self.after,
            "points": [[round(x, 3), round(y, 3)] for x, y in self.points],
        }


@dataclass
class Diff:
    alignment: Alignment
    changes: list[Change]
    unchanged: int

    def counts(self) -> dict[str, int]:
        tally = Counter(change.change for change in self.changes)
        return {
            "added": tally["added"],
            "removed": tally["removed"],
            "changed": tally["changed"],
            "unchanged": self.unchanged,
        }


def grid_lines(grid: dict[str, list[list[object]]] | None) -> GridLines | None:
    """A stored grid system (`GridSystem.as_json`) as labelled positions along each axis."""
    if not grid:
        return None
    return {
        axis: {str(label): float(str(position)) for label, position in grid.get(axis, [])}
        for axis in ("across", "up")
    }


def _axis(old: dict[str, float], new: dict[str, float]) -> tuple[float, float] | None:
    """Scale and offset along one axis from the grid lines both sheets label alike."""
    common = sorted(set(old) & set(new))
    if len(common) < 2:
        return None
    a, b = common[0], common[-1]
    if old[a] == old[b]:
        return None
    scale = (new[b] - new[a]) / (old[b] - old[a])
    return scale, new[a] - scale * old[a]


def by_grid(old: GridLines | None, new: GridLines | None) -> Alignment | None:
    if not old or not new:
        return None
    across = _axis(old.get("across", {}), new.get("across", {}))
    up = _axis(old.get("up", {}), new.get("up", {}))
    if across is None or up is None:
        return None
    return Alignment("grid", (across[0], up[0]), (across[1], up[1]))


def by_frame(old: Frame | None, new: Frame | None) -> Alignment | None:
    if old is None or new is None:
        return None
    width, height = old[2] - old[0], old[3] - old[1]
    if width <= 0 or height <= 0:
        return None
    sx, sy = (new[2] - new[0]) / width, (new[3] - new[1]) / height
    return Alignment("frame", (sx, sy), (new[0] - sx * old[0], new[1] - sy * old[1]))


def by_fit(old: list[Element], new: list[Element]) -> Alignment | None:
    """The offset most pairs of like symbols agree on. Scale is taken as unchanged."""
    votes: Counter[tuple[int, int]] = Counter()
    recent: dict[str, list[Element]] = {}
    for element in new:
        if element.kind != "run":
            recent.setdefault(element.object_type, []).append(element)
    for element in old:
        if element.kind == "run":
            continue
        for other in recent.get(element.object_type, ()):
            votes[
                (
                    round((other.x - element.x) / FIT_STEP_MM),
                    round((other.y - element.y) / FIT_STEP_MM),
                )
            ] += 1
    if not votes:
        return None
    (dx, dy), count = votes.most_common(1)[0]
    if count < 3:
        return None
    return Alignment("fit", (1.0, 1.0), (dx * FIT_STEP_MM, dy * FIT_STEP_MM))


def _matched(alignment: Alignment, old: list[Element], new: list[Element]) -> float:
    symbols = [e for e in old if e.kind != "run"]
    if not symbols:
        return 1.0
    found = 0
    for element in symbols:
        x, y = alignment.apply((element.x, element.y))
        if any(
            other.kind != "run" and math.dist((x, y), (other.x, other.y)) <= SAME_PLACE_MM
            for other in new
        ):
            found += 1
    return found / len(symbols)


def align(
    old: list[Element],
    new: list[Element],
    *,
    old_grid: GridLines | None = None,
    new_grid: GridLines | None = None,
    old_frame: Frame | None = None,
    new_frame: Frame | None = None,
) -> Alignment:
    """The first of grid, frame and fit that brings enough of the older sheet onto the
    newer; the best of them when none does; the sheets as they are when there is none."""
    candidates = [
        by_grid(old_grid, new_grid),
        by_frame(old_frame, new_frame),
        by_fit(old, new),
    ]
    scored = [
        Alignment(c.method, c.scale, c.offset, _matched(c, old, new)) for c in candidates if c
    ]
    for candidate in scored:
        if candidate.matched >= ENOUGH:
            return candidate
    if scored:
        return max(scored, key=lambda c: c.matched)
    identity = Alignment("identity")
    return Alignment("identity", matched=_matched(identity, old, new))


def _moved(element: Element, alignment: Alignment) -> Element:
    x, y = alignment.apply((element.x, element.y))
    return Element(
        element.id,
        element.kind,
        element.object_type,
        x,
        y,
        element.attributes,
        tuple(alignment.apply(point) for point in element.points),
        element.length_mm,
    )


def _stated(element: Element) -> dict[str, Any]:
    stated = {"object_type": element.object_type, **element.attributes}
    if element.length_mm is not None:
        stated["length_mm"] = round(element.length_mm)
    return stated


def _same_ends(first: Element, second: Element) -> bool:
    a, b = first.ends, second.ends
    if a is None or b is None:
        return False
    reach = 2 * SAME_PLACE_MM
    return (math.dist(a[0], b[0]) <= reach and math.dist(a[1], b[1]) <= reach) or (
        math.dist(a[0], b[1]) <= reach and math.dist(a[1], b[0]) <= reach
    )


def _heading(start: Point, towards: Point) -> Point:
    size = math.dist(start, towards) or 1.0
    return ((towards[0] - start[0]) / size, (towards[1] - start[1]) / size)


def _same_start(first: Element, second: Element) -> bool:
    """Two runs leaving one point in one direction: the same pipe, now longer or shorter."""
    for a in (first.points, first.points[::-1]):
        for b in (second.points, second.points[::-1]):
            if len(a) < 2 or len(b) < 2 or math.dist(a[0], b[0]) > 2 * SAME_PLACE_MM:
                continue
            da, db = _heading(a[0], a[1]), _heading(b[0], b[1])
            if da[0] * db[0] + da[1] * db[1] > 0.99:
                return True
    return False


def diff(
    old: list[Element],
    new: list[Element],
    *,
    old_grid: GridLines | None = None,
    new_grid: GridLines | None = None,
    old_frame: Frame | None = None,
    new_frame: Frame | None = None,
) -> Diff:
    alignment = align(
        old, new, old_grid=old_grid, new_grid=new_grid, old_frame=old_frame, new_frame=new_frame
    )
    before = [_moved(element, alignment) for element in old]
    changes: list[Change] = []
    unchanged = 0
    gone = {e.id: e for e in before}
    come = {e.id: e for e in new}

    def pair(first: Element, second: Element) -> None:
        nonlocal unchanged
        gone.pop(first.id)
        come.pop(second.id)
        was, now = _stated(first), _stated(second)
        if was == now:
            unchanged += 1
            return
        changes.append(
            Change(
                "changed",
                second.kind,
                second.object_type,
                second.x,
                second.y,
                first.id,
                second.id,
                {k: v for k, v in was.items() if now.get(k) != v},
                {k: v for k, v in now.items() if was.get(k) != v},
                second.points,
            )
        )

    # Symbols: the same thing at the same place first, then anything at the same place.
    for same_type in (True, False):
        near = sorted(
            (math.dist((a.x, a.y), (b.x, b.y)), a.id, b.id)
            for a in gone.values()
            for b in come.values()
            if a.kind != "run"
            and b.kind != "run"
            and a.kind == b.kind
            and (a.object_type == b.object_type) is same_type
            and math.dist((a.x, a.y), (b.x, b.y)) <= SAME_PLACE_MM
        )
        for _, first, second in near:
            if first in gone and second in come:
                pair(gone[first], come[second])
    # Runs: both ends alike, then one end and the direction alike.
    for alike in (_same_ends, _same_start):
        for was_run in [e for e in gone.values() if e.kind == "run"]:
            now_run = next(
                (e for e in come.values() if e.kind == "run" and alike(was_run, e)), None
            )
            if now_run is not None:
                pair(was_run, now_run)

    for element in gone.values():
        changes.append(
            Change(
                "removed",
                element.kind,
                element.object_type,
                element.x,
                element.y,
                old_id=element.id,
                before=_stated(element),
                points=element.points,
            )
        )
    for element in come.values():
        changes.append(
            Change(
                "added",
                element.kind,
                element.object_type,
                element.x,
                element.y,
                new_id=element.id,
                after=_stated(element),
                points=element.points,
            )
        )
    order = {"changed": 0, "added": 1, "removed": 2}
    changes.sort(key=lambda c: (order[c.change], c.kind, c.object_type, round(c.y), round(c.x)))
    return Diff(alignment, changes, unchanged)

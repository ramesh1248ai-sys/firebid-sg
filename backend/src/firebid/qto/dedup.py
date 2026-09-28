"""Suspected duplicates across sheets and views (FR-QTO-08).

Views of one level are compared in grid units, which P1-03 made the same on every sheet
whatever its scale. Three kinds of repetition are found:

* **enlarged plan:** a symbol on an enlarged plan at the grid position of the same symbol on
  a general plan of the same level;
* **match line:** two plans of one level that overlap, with the same symbol at the same grid
  position on both, and pipe drawn along the same line on both;
* **schematic or section:** everything on a schematic, section, elevation or detail view. By
  default it does not count against plan items: its group is created resolved as excluded,
  with the reason, and a person may reverse it.

One group per pair of sheets (or per schematic sheet), listing every repeated member with
its evidence location. In a plan group the general plan's member is kept and the other is
excluded until a person decides; a repeated run keeps its length on one sheet and the
overlapping length is taken off the other. An unresolved group blocks G1.

Pure: detections and runs with their grid positions in; groups out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from firebid.qto.model import Detection, Run, key_of

NOT_PLANS = ("schematic", "section", "elevation", "detail")
PLANS = ("plan", "enlarged plan", "key plan", None)
# Two symbols within this many bay-widths of each other are at the same place.
SAME_PLACE = 0.03
# Two runs within this of the same line, in bay-widths, are along the same line.
SAME_LINE = 0.02


@dataclass
class Group:
    kind: str  # enlarged_plan | match_line | schematic
    level: str | None
    status: str  # unresolved | auto_excluded
    reason: str
    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        sheets = sorted({m["sheet_id"] for m in self.members})
        return key_of(self.kind, self.level, sheets)


def _rank(view_kind: str | None, sheet_number: str) -> tuple[int, str]:
    """Which of two repeated members is kept: the general plan, then the lower number."""
    return (0 if view_kind in ("plan", None) else 1, sheet_number)


def _member(item: Detection | Run, keep: bool, excluded_length: int = 0) -> dict[str, Any]:
    at = item.at
    base: dict[str, Any] = {
        "kind": "detection" if isinstance(item, Detection) else "run",
        "id": item.id,
        "sheet_id": at.sheet_id,
        "sheet_number": at.sheet_number,
        "view_kind": at.view_kind,
        "grid_reference": item.grid_reference,
        "keep": keep,
    }
    if isinstance(item, Detection):
        base.update(
            object_type=item.object_type,
            detection_kind=item.kind,
            x=round(item.x, 3),
            y=round(item.y, 3),
        )
    else:
        base.update(dn=item.dn, length_mm=item.length_mm, excluded_length_mm=excluded_length)
    return base


def find(detections: list[Detection], runs: list[Run]) -> list[Group]:
    groups: dict[str, Group] = {}

    # Schematics and sections: excluded by default, one group per sheet.
    everything: list[Detection | Run] = [*detections, *runs]
    for item in everything:
        if item.at.view_kind in NOT_PLANS:
            key = key_of("schematic", item.at.sheet_id)
            group = groups.setdefault(
                key,
                Group(
                    "schematic",
                    item.at.level,
                    "auto_excluded",
                    f"{item.at.view_kind} views repeat plan items and are not counted "
                    "against them by default",
                ),
            )
            group.members.append(_member(item, keep=False))

    plan_detections = [d for d in detections if d.at.view_kind in PLANS and d.grid_index]
    by_level: dict[str | None, list[Detection]] = defaultdict(list)
    for detection in plan_detections:
        by_level[detection.at.level].append(detection)
    for level, items in by_level.items():
        for index, first in enumerate(items):
            for second in items[index + 1 :]:
                if first.at.sheet_id == second.at.sheet_id:
                    continue
                if (first.object_type, first.kind) != (second.object_type, second.kind):
                    continue
                a, b = first.grid_index, second.grid_index
                if a is None or b is None:  # filtered above; for the type checker
                    continue
                if abs(a[0] - b[0]) > SAME_PLACE or abs(a[1] - b[1]) > SAME_PLACE:
                    continue
                kept, other = sorted(
                    (first, second), key=lambda d: _rank(d.at.view_kind, d.at.sheet_number)
                )
                group = _pair_group(groups, level, kept, other)
                _add_once(group, _member(kept, keep=True))
                _add_once(group, _member(other, keep=False))

    plan_runs = [r for r in runs if r.at.view_kind in PLANS and r.length_mm]
    runs_by_level: dict[str | None, list[Run]] = defaultdict(list)
    for run in plan_runs:
        runs_by_level[run.at.level].append(run)
    for level, run_items in runs_by_level.items():
        for index, first_run in enumerate(run_items):
            for second_run in run_items[index + 1 :]:
                if first_run.at.sheet_id == second_run.at.sheet_id:
                    continue
                overlap = _overlap(first_run, second_run)
                if overlap is None:
                    continue
                kept_run, other_run = sorted(
                    (first_run, second_run),
                    key=lambda r: _rank(r.at.view_kind, r.at.sheet_number),
                )
                length = other_run.length_mm or 0
                per_unit = _mm_per_grid_unit(other_run)
                taken = min(length, round(overlap * per_unit)) if per_unit else 0
                if taken <= 0:
                    continue
                group = _pair_group(groups, level, kept_run, other_run)
                _add_once(group, _member(kept_run, keep=True))
                member = _member(other_run, keep=False, excluded_length=taken)
                if other_run.dn is None and kept_run.dn is not None:
                    # A size written on one side of a match line applies on the other.
                    member["carried_dn"] = kept_run.dn
                    member["carried_from"] = kept_run.at.sheet_number
                group.members.append(member)
    return list(groups.values())


def _pair_group(
    groups: dict[str, Group], level: str | None, kept: Detection | Run, other: Detection | Run
) -> Group:
    kind = (
        "enlarged_plan"
        if "enlarged plan" in (kept.at.view_kind, other.at.view_kind)
        else "match_line"
    )
    key = key_of(kind, level, sorted((kept.at.sheet_id, other.at.sheet_id)))
    return groups.setdefault(
        key,
        Group(
            kind,
            level,
            "unresolved",
            f"{other.at.sheet_number} repeats {kept.at.sheet_number} "
            f"({'an enlarged plan' if kind == 'enlarged_plan' else 'across a match line'}); "
            f"counted once from {kept.at.sheet_number} until a person decides",
        ),
    )


def _add_once(group: Group, member: dict[str, Any]) -> None:
    if not any(m["kind"] == member["kind"] and m["id"] == member["id"] for m in group.members):
        group.members.append(member)


def _grid_line(run: Run) -> tuple[str, float, float, float] | None:
    """A straight run in grid units: its axis, its offset, and its extent along the axis."""
    points = [p for p in run.grid_points if p is not None]
    if len(points) < 2:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if max(ys) - min(ys) <= SAME_LINE:
        return ("x", sum(ys) / len(ys), min(xs), max(xs))
    if max(xs) - min(xs) <= SAME_LINE:
        return ("y", sum(xs) / len(xs), min(ys), max(ys))
    return None


def _mm_per_grid_unit(run: Run) -> float:
    """Drawing millimetres per grid unit along a run, from its view's scale."""
    points = [(p, g) for p, g in zip(run.points, run.grid_points, strict=True) if g is not None]
    if len(points) < 2 or not run.scale:
        return 0.0
    (p0, g0), (p1, g1) = points[0], points[-1]
    sheet = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
    grid = ((g1[0] - g0[0]) ** 2 + (g1[1] - g0[1]) ** 2) ** 0.5
    return sheet * run.scale / grid if grid else 0.0


def _grid_length(run: Run) -> float:
    line = _grid_line(run)
    return (line[3] - line[2]) if line else 0.0


def _overlap(first: Run, second: Run) -> float | None:
    """How far, in grid units, two runs lie along the same line."""
    a, b = _grid_line(first), _grid_line(second)
    if a is None or b is None or a[0] != b[0] or abs(a[1] - b[1]) > SAME_LINE:
        return None
    shared = min(a[3], b[3]) - max(a[2], b[2])
    return shared if shared > SAME_LINE else None


def carried_sizes(groups: list[Group]) -> dict[str, tuple[int, str]]:
    """Runs whose size was written only on the other side of a match line."""
    carried: dict[str, tuple[int, str]] = {}
    for group in groups:
        for member in group.members:
            if member.get("carried_dn"):
                carried[member["id"]] = (int(member["carried_dn"]), str(member["carried_from"]))
    return carried


def exclusions(
    groups: list[Group], decisions: dict[str, str] | None = None
) -> tuple[set[str], dict[str, int]]:
    """What the groups take out of the count: excluded detections, and run lengths.

    `decisions` maps a group key to a person's decision: `confirmed` keeps the exclusions,
    `not_duplicate` counts every member. Undecided groups keep their default exclusions.
    """
    excluded: set[str] = set()
    lengths: dict[str, int] = {}
    for group in groups:
        if (decisions or {}).get(group.key) == "not_duplicate":
            continue
        for member in group.members:
            if member["keep"]:
                continue
            if member["kind"] == "detection":
                excluded.add(member["id"])
            elif member.get("excluded_length_mm"):
                lengths[member["id"]] = lengths.get(member["id"], 0) + member["excluded_length_mm"]
            else:
                excluded.add(member["id"])  # a schematic's run: the whole of it
    return excluded, lengths

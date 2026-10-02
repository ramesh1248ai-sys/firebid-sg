"""Finding repeats without comparing every pair (FR-QTO-08; NFR-01).

`dedup.find` compared every detection of a level with every other, and every run with every
other: thousands of each on a real tender. It now looks each one up among its neighbours.
The all-pairs search is kept here, as written, as the reference: on any detections and runs
the groups must be the same, with the same members in the same order.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.qto import dedup
from firebid.qto.dedup import (
    PLANS,
    SAME_PLACE,
    Group,
    _add_once,
    _member,
    _mm_per_grid_unit,
    _overlap,
    _pair_group,
    _rank,
)
from firebid.qto.model import Detection, Placement, Run

pytestmark = [pytest.mark.req("FR-QTO-08"), pytest.mark.req("NFR-01")]


def reference(detections: list[Detection], runs: list[Run]) -> list[Group]:
    """The plan part of `find`, comparing every pair, as it was before."""
    groups: dict[str, Group] = {}
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
                if a is None or b is None:
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
                    member["carried_dn"] = kept_run.dn
                    member["carried_from"] = kept_run.at.sheet_number
                group.members.append(member)
    return list(groups.values())


def told(groups: list[Group]) -> list[tuple[Any, ...]]:
    return [(g.kind, g.level, g.status, g.reason, g.key, g.members) for g in groups]


SHEETS = ("FP-L05-201", "FP-L05-202", "FP-L05-301")
KINDS = {"FP-L05-201": "plan", "FP-L05-202": "plan", "FP-L05-301": "enlarged plan"}


def place(sheet: str, level: str | None) -> Placement:
    return Placement(sheet, sheet, "R01", "doc", f"view-{sheet}", KINDS[sheet], level, None)


# Grid positions on a lattice a third of `SAME_PLACE` wide: the same place, just inside it,
# just outside it, and far away all occur.
position = st.integers(min_value=0, max_value=40).map(lambda v: v * SAME_PLACE / 3)
level = st.sampled_from(["L05", "L06", None])


@st.composite
def detection(draw: st.DrawFn, index: int) -> Detection:
    sheet = draw(st.sampled_from(SHEETS))
    return Detection(
        id=f"d{index}",
        at=place(sheet, draw(level)),
        kind=draw(st.sampled_from(["object", "drop"])),
        object_type=draw(st.sampled_from(["sprinkler_pendent", "gate_valve"])),
        category="sprinkler",
        attributes={},
        x=float(index),
        y=0.0,
        grid_reference="Grid A1",
        grid_index=draw(st.one_of(st.none(), st.tuples(position, position))),
        confidence=0.9,
        method="pdf_shape",
    )


@st.composite
def run(draw: st.DrawFn, index: int) -> Run:
    sheet = draw(st.sampled_from(SHEETS))
    offset, start = draw(position), draw(position)
    length = draw(st.integers(min_value=0, max_value=30).map(lambda v: v * SAME_PLACE / 3))
    horizontal = draw(st.booleans())
    ends = (
        ((start, offset), (start + length, offset))
        if horizontal
        else ((offset, start), (offset, start + length))
    )
    return Run(
        id=f"r{index}",
        at=place(sheet, draw(level)),
        run_class="branch",
        dn=draw(st.sampled_from([None, 25, 50])),
        size_status="labelled",
        length_mm=draw(st.sampled_from([None, 0, 3000, 12000])),
        points=((0.0, 0.0), (100.0, 0.0)),
        grid_reference="Grid A1",
        grid_points=draw(st.sampled_from([ends, (None, None), (ends[0], None)])),
        confidence=0.9,
        scale=draw(st.sampled_from([None, 100.0])),
    )


@settings(max_examples=300, deadline=None)
@given(data=st.data())
def test_the_groups_are_the_all_pairs_search_s(data: st.DataObject) -> None:
    detections = [data.draw(detection(i)) for i in range(data.draw(st.integers(0, 40)))]
    runs = [data.draw(run(i)) for i in range(data.draw(st.integers(0, 25)))]

    assert told(dedup.find(detections, runs)) == told(reference(detections, runs))


def test_a_level_of_thousands_is_searched_without_comparing_every_pair() -> None:
    # Two sheets of one level that overlap in a strip: 4,000 detections on each.
    detections = []
    for sheet in SHEETS[:2]:
        for index in range(4000):
            detections.append(
                Detection(
                    id=f"{sheet}-{index}",
                    at=place(sheet, "L05"),
                    kind="object",
                    object_type="sprinkler_pendent",
                    category="sprinkler",
                    attributes={},
                    x=0.0,
                    y=0.0,
                    grid_reference="Grid A1",
                    grid_index=((index % 80) * 0.5, (index // 80) * 0.5),
                    confidence=0.9,
                    method="pdf_shape",
                )
            )

    started = time.perf_counter()
    groups = dedup.find(detections, [])
    seconds = time.perf_counter() - started

    assert len(groups) == 1 and len(groups[0].members) == 8000
    # Every pair was 32 million comparisons: minutes. A generous bound, for a slow machine.
    assert seconds < 60.0

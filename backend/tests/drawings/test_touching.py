"""Grouping the strokes of a symbol: boxes that touch, drawn in one pen (FR-VIS-02; NFR-01).

`_touching` was a Python loop and a third of a real sheet's reading time; it is now done in
arrays. The loop is kept here, as written, as the reference: on any set of boxes the two
must give the same groups, in the same order.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.drawings import symbols
from firebid.drawings.symbols import TOUCH_MM, _touching

pytestmark = pytest.mark.req("NFR-01")


def reference(columns: dict[str, list[Any]], rows: list[int]) -> list[list[int]]:
    """The union-find over a sort-and-sweep that `_touching` replaced."""
    parent = {row: row for row in rows}

    def root(row: int) -> int:
        while parent[row] != row:
            parent[row] = parent[parent[row]]
            row = parent[row]
        return row

    ordered = sorted(rows, key=lambda row: columns["minx"][row])
    active: list[int] = []
    for row in ordered:
        left = columns["minx"][row] - TOUCH_MM
        active = [other for other in active if columns["maxx"][other] >= left]
        for other in active:
            if (
                columns["layer"][other] == columns["layer"][row]
                and columns["color"][other] == columns["color"][row]
                and columns["miny"][other] <= columns["maxy"][row] + TOUCH_MM
                and columns["maxy"][other] >= columns["miny"][row] - TOUCH_MM
            ):
                parent[root(other)] = root(row)
        active.append(row)
    groups: dict[int, list[int]] = {}
    for row in rows:
        groups.setdefault(root(row), []).append(row)
    return list(groups.values())


def columns_of(
    boxes: Sequence[tuple[float, float, float, float, str | None, int]],
) -> dict[str, Any]:
    return {
        "minx": [b[0] for b in boxes],
        "miny": [b[1] for b in boxes],
        "maxx": [b[0] + b[2] for b in boxes],
        "maxy": [b[1] + b[3] for b in boxes],
        "layer": [b[4] for b in boxes],
        "color": [b[5] for b in boxes],
    }


def as_sets(groups: list[list[int]]) -> list[list[int]]:
    """The reference names a group by a root, the arrays by a label: the groups, each in its
    rows' order, in the order of their first rows, are what must agree."""
    return sorted(groups, key=lambda group: group[0])


# Coordinates on a 0.1 mm lattice in a small area, so boxes touch, nearly touch and overlap.
coordinate = st.integers(min_value=0, max_value=300).map(lambda v: v / 10)
size = st.integers(min_value=0, max_value=40).map(lambda v: v / 10)
box = st.tuples(
    coordinate,
    coordinate,
    size,
    size,
    st.sampled_from(["FP-SPRINKLER", "FP-PIPE", None]),
    st.sampled_from([0xFF0000, 0x00FF00]),
)


@settings(max_examples=300, deadline=None)
@given(boxes=st.lists(box, max_size=60), data=st.data())
def test_the_groups_are_the_reference_s(
    boxes: list[tuple[float, float, float, float, str | None, int]], data: st.DataObject
) -> None:
    columns = columns_of(boxes)
    # Any subset of the rows, in any order: `clusters` passes only the loose, symbol-sized.
    rows = data.draw(st.permutations(range(len(boxes))).map(lambda p: list(p)[: len(p) // 2 + 1]))
    rows = [row for row in rows if row < len(boxes)]

    assert as_sets(_touching(columns, rows)) == as_sets(reference(columns, rows))


def test_no_rows_no_groups() -> None:
    assert _touching(columns_of([]), []) == []


def test_boxes_a_hair_apart_join_and_further_apart_do_not() -> None:
    near = TOUCH_MM - 0.05
    far = TOUCH_MM + 0.05
    columns = columns_of(
        [
            (0.0, 0.0, 1.0, 1.0, "L", 1),
            (1.0 + near, 0.0, 1.0, 1.0, "L", 1),  # within the touch distance of the first
            (2.0 + near + far, 0.0, 1.0, 1.0, "L", 1),  # beyond it from the second
        ]
    )

    assert _touching(columns, [0, 1, 2]) == [[0, 1], [2]]


def test_another_pen_never_joins() -> None:
    columns = columns_of(
        [(0.0, 0.0, 2.0, 2.0, "FP-SPRINKLER", 1), (1.0, 1.0, 2.0, 2.0, "FP-PIPE", 1)]
    )

    assert _touching(columns, [0, 1]) == [[0], [1]]
    assert _touching(columns_of([(0, 0, 2, 2, "L", 1), (1, 1, 2, 2, "L", 2)]), [0, 1]) == [[0], [1]]


def test_a_chain_is_one_group_however_long(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each box touches only the next: the group's label has to travel the whole chain. With
    # a small chunk, the pairs are also found across several chunks.
    monkeypatch.setattr(symbols, "PAIR_CHUNK", 7)
    columns = columns_of([(float(i), 0.0, 1.0, 1.0, "L", 1) for i in range(200)])

    assert _touching(columns, list(range(200))) == [list(range(200))]


def test_a_dense_sheet_is_grouped_faster_than_the_loop_did() -> None:
    # 30,000 small strokes scattered over an A1 sheet, as a busy plan has. Timed against
    # the loop on the same strokes, so the test holds on a slow machine as on a fast one.
    rng = np.random.default_rng(7)
    count = 30_000
    boxes = [
        (float(x), float(y), float(w), float(h), "L", 1)
        for x, y, w, h in zip(
            rng.uniform(0, 841, count),
            rng.uniform(0, 594, count),
            rng.uniform(0.2, 3.0, count),
            rng.uniform(0.2, 3.0, count),
            strict=True,
        )
    ]
    columns = columns_of(boxes)
    rows = list(range(count))

    # The best of three: one run alone can take in a pause that is not the grouping's (a
    # garbage collection of the test process, as seen on Python 3.14).
    seconds = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        groups = _touching(columns, rows)
        seconds = min(seconds, time.perf_counter() - started)
    started = time.perf_counter()
    expected = reference(columns, rows)
    loop_seconds = time.perf_counter() - started

    assert as_sets(groups) == as_sets(expected)
    assert seconds < loop_seconds

"""Two things done once a sheet instead of once a symbol or once a figure.

Each is checked against the plain form it replaced, kept here as the reference: the answer
has to be the same to the last bit, because a symbol's signature and a view's scale are
evidence, and a faster way of reading a sheet must not read it differently.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.drawings import scale, symbols
from firebid.drawings.geometry import Builder, Method, segments, texts
from firebid.drawings.scale import FIGURE, Evidence, in_box
from firebid.drawings.symbols import Sampler, sample

pytestmark = pytest.mark.req("NFR-01")

coordinate = st.floats(min_value=0, max_value=400, allow_nan=False, width=32)
size = st.floats(min_value=0.5, max_value=20, allow_nan=False, width=32)

primitive = st.one_of(
    st.tuples(st.just("line"), st.lists(coordinate, min_size=4, max_size=4)),
    st.tuples(st.just("open"), st.lists(coordinate, min_size=4, max_size=12)),
    st.tuples(st.just("closed"), st.lists(coordinate, min_size=4, max_size=12)),
    st.tuples(st.just("hatch"), st.lists(coordinate, min_size=6, max_size=12)),
    st.tuples(st.just("circle"), st.tuples(coordinate, coordinate, size)),
    st.tuples(st.just("dot"), st.tuples(coordinate, coordinate, st.just(0.0))),
    st.tuples(st.just("arc"), st.tuples(coordinate, coordinate, size)),
    st.tuples(st.just("text"), st.tuples(coordinate, coordinate, size)),
)


def table_of(primitives: list[tuple[str, Any]]) -> pa.Table:
    builder = Builder(next(iter(Method)))
    for kind, values in primitives:
        if kind == "line":
            builder.line(values[0], values[1], values[2], values[3], 1)
        elif kind in ("open", "closed"):
            even = values[: len(values) // 2 * 2]
            builder.polyline(even, 1, closed=kind == "closed")
        elif kind == "hatch":
            builder.hatch(values[: len(values) // 2 * 2], 1)
        elif kind in ("circle", "dot"):
            builder.circle(values[0], values[1], values[2], 1)
        elif kind == "arc":
            builder.arc(values[0], values[1], values[2], 10.0, 200.0, 1)
        else:
            x, y, height = values
            builder.text("1500", (x, y, x + 4 * height, y + height), 1, height=height)
    return builder.table()


class TestSamplingASymbol:
    @given(st.lists(primitive, min_size=1, max_size=25), st.data())
    @settings(max_examples=200, deadline=None)
    def test_a_sheet_s_sampler_gives_the_points_slicing_the_table_gave(
        self, primitives: list[tuple[str, Any]], data: st.DataObject
    ) -> None:
        table = table_of(primitives)
        if table.num_rows == 0:
            return
        sampler = Sampler(table)
        every = st.integers(min_value=0, max_value=table.num_rows - 1)
        for _ in range(4):
            rows = data.draw(st.lists(every, min_size=1, max_size=8, unique=True))
            points, directions = sampler.sample(rows)
            wanted_points, wanted_directions = sample(table, rows)
            assert np.array_equal(points, wanted_points)
            assert np.array_equal(directions, wanted_directions)

    def test_a_closed_outline_keeps_its_closing_side(self) -> None:
        builder = Builder(next(iter(Method)))
        builder.line(0, 0, 50, 0, 1)
        builder.polyline([0, 0, 10, 0, 10, 10, 0, 10], 1, closed=True)
        builder.polyline([20, 0, 30, 0, 30, 10], 1, closed=True)
        table = builder.table()
        for rows in ([1], [2], [2, 1], [0, 2]):
            points, _ = Sampler(table).sample(rows)
            assert np.array_equal(points, sample(table, rows)[0])
        # The square's left side is only there if its last point was joined to its first.
        square, _ = Sampler(table).sample([1])
        assert (square[:, 0] == 0).sum() > 8


def reference_figures(table: pa.Table, extent: tuple[float, float, float, float]) -> list[Evidence]:
    """`scale._paired_figures` as it was: every line against every figure."""
    lines = segments(table)
    if len(lines) == 0:
        return []
    mid_x, mid_y = (lines.x0 + lines.x1) / 2, (lines.y0 + lines.y1) / 2
    lengths = lines.lengths
    angles = np.degrees(np.arctan2(lines.y1 - lines.y0, lines.x1 - lines.x0)) % 180
    found = []
    for span in texts(table):
        match = FIGURE.match(span["text"] or "")
        if not match:
            continue
        cx, cy = (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2
        if not in_box(cx, cy, extent):
            continue
        height = max(float(span["height"] or 0), span["maxy"] - span["miny"], 0.5)
        width = span["maxx"] - span["minx"]
        rotation = float(span["rotation"] or 0) % 180
        along = np.abs(np.cos(np.radians(angles))) * np.abs(mid_x - cx) + np.abs(
            np.sin(np.radians(angles))
        ) * np.abs(mid_y - cy)
        across = np.hypot(mid_x - cx, mid_y - cy)
        parallel = np.minimum(np.abs(angles - rotation), 180 - np.abs(angles - rotation)) < 3
        candidates = np.flatnonzero(
            parallel & (lengths > width) & (across < 3 * height) & (along < 0.25 * lengths)
        )
        if candidates.size:
            best = candidates[np.argmin(across[candidates])]
            found.append(Evidence(float(match.group(1)), float(lengths[best]), "figure", (cx, cy)))
    return found


# Figures over a small sheet of horizontal and vertical lines, so that many figures have
# several lines to choose between, some of them equally near.
grid = st.integers(min_value=0, max_value=12).map(lambda step: step * 5.0)
drawn_line = st.tuples(grid, grid, st.sampled_from([10.0, 20.0, 40.0]), st.booleans())
figure = st.tuples(grid, grid, st.sampled_from([1.0, 2.5]), st.sampled_from([0.0, 90.0]))


class TestFiguresOnTheirLines:
    @given(st.lists(drawn_line, max_size=30), st.lists(figure, max_size=10))
    @settings(max_examples=200, deadline=None)
    def test_the_figures_found_are_the_ones_every_line_against_every_figure_found(
        self,
        drawn: list[tuple[float, float, float, bool]],
        figures: list[tuple[float, float, float, float]],
    ) -> None:
        builder = Builder(next(iter(Method)))
        for x, y, length, upright in drawn:
            builder.line(x, y, x if upright else x + length, y + length if upright else y, 1)
        for x, y, height, rotation in figures:
            box = (x - 2, y + 0.5, x + 2, y + 0.5 + height)
            builder.text("1500", box, 1, height=height, rotation=rotation)
        table = builder.table()
        for extent in ((-10.0, -10.0, 200.0, 200.0), (0.0, 0.0, 30.0, 30.0)):
            assert scale._paired_figures(table, extent) == reference_figures(table, extent)

    def test_a_figure_on_its_line_is_paired_with_it(self) -> None:
        builder = Builder(next(iter(Method)))
        builder.line(0, 10, 40, 10, 1)
        builder.line(0, 30, 100, 30, 1)
        builder.text("4000", (18, 10.5, 22, 12.5), 1, height=2.0)
        (found,) = scale._paired_figures(builder.table(), (0.0, 0.0, 200.0, 200.0))
        assert (found.value, found.paper_mm) == (4000.0, 40.0)


def reference_describe(
    points: np.ndarray, directions: np.ndarray
) -> tuple[tuple[float, ...], float] | None:
    """`symbols.describe` as it was: a full square of differences, counted by `np.histogram`."""
    if len(points) < 8:
        return None
    centred = points - points.mean(axis=0)
    radii = np.hypot(centred[:, 0], centred[:, 1])
    rms = float(np.sqrt(np.mean(radii**2)))
    if rms <= 1e-9:
        return None
    difference = centred[:, None, :] - centred[None, :, :]
    pairwise = np.hypot(difference[..., 0], difference[..., 1])[np.triu_indices(len(points), 1)]
    d2, _ = np.histogram(pairwise / rms, bins=symbols.D2_BINS)
    radial, _ = np.histogram(radii / rms, bins=symbols.RADIAL_BINS)
    outward = centred / np.maximum(radii, 1e-9)[:, None]
    along = np.abs((outward * directions).sum(axis=1))
    turn, _ = np.histogram(along[radii > 1e-6 * rms], bins=symbols.TURN_BINS)
    descriptor = np.concatenate([part / max(part.sum(), 1) for part in (d2, radial, turn)])
    return tuple(float(value) for value in descriptor), rms


on_or_near_an_edge = st.sampled_from([0.0, 0.2, 0.4, 3.0, 3.2, 3.2000000000000006, 3.4, -0.1])
measured = st.one_of(on_or_near_an_edge, st.floats(min_value=-1, max_value=5, allow_nan=False))


class TestTheDescriptor:
    @given(st.lists(measured, max_size=60))
    @settings(max_examples=300, deadline=None)
    def test_values_are_counted_as_a_histogram_counts_them(self, values: list[float]) -> None:
        array = np.asarray(values, dtype=float)
        wanted, _ = np.histogram(array, bins=symbols.D2_BINS)
        assert np.array_equal(symbols._counted(array, symbols.D2_BINS), wanted)

    @given(st.lists(primitive, min_size=1, max_size=12))
    @settings(max_examples=150, deadline=None)
    def test_a_symbol_s_descriptor_is_the_one_the_full_square_gave(
        self, primitives: list[tuple[str, Any]]
    ) -> None:
        table = table_of(primitives)
        if table.num_rows == 0:
            return
        points, directions = sample(table, list(range(table.num_rows)))
        assert symbols.describe(points, directions) == reference_describe(points, directions)

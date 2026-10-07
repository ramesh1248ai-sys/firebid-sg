"""The grid of a real plan: gridlines drawn dashed, and a wing at an angle (FR-VIS-07).

Found on a real tender of 148 sheets: every gridline is a chain line, so the detector, which
wanted one long line ending at each bubble, found no grid on any of them and nothing on
them had a grid reference. A wing of the building stands at 38 degrees to the sheet with
gridlines of its own, beside a main block whose gridlines are square to it.
"""

from __future__ import annotations

import math

import pyarrow as pa
import pytest

from firebid.drawings import geometry, grids
from firebid.evals import synthetic
from firebid.parsing.geometry_dxf import extract as extract_dxf

RADIUS = 5.0


def dashed(
    builder: geometry.Builder,
    start: tuple[float, float],
    angle: float,
    length: float,
    *,
    dash: float = 12.0,
    gap: float = 4.0,
) -> None:
    """A chain line from a point, at an angle from the x axis."""
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    along = 0.0
    while along < length:
        end = min(along + dash, length)
        builder.line(
            start[0] + cos * along,
            start[1] + sin * along,
            start[0] + cos * end,
            start[1] + sin * end,
            0,
        )
        along = end + gap


def bubble(
    builder: geometry.Builder, label: str, cx: float, cy: float, radius: float = RADIUS
) -> None:
    builder.circle(cx, cy, radius, 0)
    builder.text(label, (cx - 2, cy - 2, cx + 2, cy + 2), 0, height=3.0)


def gridline(
    builder: geometry.Builder, label: str, cx: float, cy: float, angle: float, length: float
) -> None:
    """A bubble and the chain line that runs from its edge."""
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    bubble(builder, label, cx, cy)
    dashed(builder, (cx + cos * RADIUS, cy + sin * RADIUS), angle, length)


def square_plan(across: str = "A B C D", up: str = "1 2 3") -> geometry.Builder:
    """Letters along the top with lines down the sheet; numbers down the left with lines
    across it. All dashed."""
    builder = geometry.Builder(geometry.Method.PDF_VECTOR)
    for n, label in enumerate(across.split()):
        gridline(builder, label, 100.0 + 84.0 * n, 20.0, 90.0, 500.0)
    for n, label in enumerate(up.split()):
        gridline(builder, label, 20.0, 100.0 + 84.0 * n, 0.0, 700.0)
    return builder


def wing(builder: geometry.Builder, turn: float = 38.4) -> None:
    """A wing at an angle: letters K to N down the left edge with lines running up to the
    right, and numbers 1 to 3 whose lines run square to those."""
    for n, label in enumerate(["K", "L", "M", "N"]):
        gridline(builder, label, 30.0, 300.0 + 100.0 * n, -turn, 300.0)
    for n, label in enumerate(["1", "2", "3"]):
        gridline(builder, label, 60.0 + 70.0 * n, 200.0 - 10.0 * n, 90.0 - turn, 400.0)


def detected(builder: geometry.Builder) -> grids.GridSystem | None:
    return grids.detect(builder.table())


@pytest.mark.req("FR-VIS-07")
class TestDashedGridlines:
    def test_a_grid_drawn_in_chain_lines_is_found(self) -> None:
        grid = detected(square_plan())

        assert grid is not None
        assert [line.label for line in grid.across] == ["A", "B", "C", "D"]
        assert [line.label for line in grid.up] == ["1", "2", "3"]
        assert [line.position for line in grid.across] == [100.0, 184.0, 268.0, 352.0]
        assert all(line.slope == 0 for line in (*grid.across, *grid.up)), "square to the sheet"
        assert grid.reference(184.0, 184.0) == "Grid B2"
        assert grid.reference(200.0, 200.0) == f"Grid B2{grids.BAY}C3"

    def test_a_line_knows_how_far_it_is_drawn_and_names_nothing_beyond(self) -> None:
        grid = detected(square_plan())
        assert grid is not None
        a = grid.across[0]

        # Drawn from the bubble's edge at y = 25 for 500 mm down the sheet.
        assert a.low == pytest.approx(25.0) and a.high == pytest.approx(525.0)
        assert grid.reference(200.0, 200.0) is not None
        # Between A's and B's lines if they went on, but 200 mm past where they stop.
        assert grid.reference(140.0, 730.0) is None

    def test_a_long_line_ending_at_a_bubble_is_found_as_it_always_was(self) -> None:
        result = extract_dxf(synthetic.dxf_bytes(synthetic.general_arrangement()[0]), None)
        grid = grids.detect(geometry.from_parquet(result["parquet"]))

        assert grid is not None and grid.one_grid
        assert all(line.low is None and line.slope == 0 for line in (*grid.across, *grid.up))
        assert grid.index(grid.across[1].position, grid.up[1].position) == (2.0, 2.0)
        # Stored as it was: a label and a position, nothing more.
        assert all(len(row) == 2 for rows in grid.as_json().values() for row in rows)

    def test_a_grid_found_dashed_gives_references_and_no_index(self) -> None:
        # It may be one grid of several on the sheet, and the takeoff counts two things at
        # one index once: so it names a place and offers no coordinates shared with others.
        grid = detected(square_plan())

        assert grid is not None and not grid.one_grid
        assert grid.index(200.0, 200.0) is None
        assert grid.box((100.0, 100.0, 300.0, 250.0)) is None

    def test_it_survives_storage(self) -> None:
        builder = geometry.Builder(geometry.Method.PDF_VECTOR)
        wing(builder)
        grid = detected(builder)
        assert grid is not None

        again = grids.GridSystem.from_json(grid.as_json())

        assert again == grid
        assert all(len(row) == 6 for rows in grid.as_json().values() for row in rows)

    def test_a_pipe_through_a_sprinkler_is_not_a_gridline(self) -> None:
        # A sprinkler is a letter in a small circle, with a pipe running through it.
        builder = square_plan()
        for n in range(4):
            bubble(builder, "S", 300.0 + 40.0 * n, 400.0, radius=2.5)
        builder.line(250.0, 400.0, 500.0, 400.0, 0)

        grid = detected(builder)

        assert grid is not None
        assert "S" not in [line.label for line in (*grid.across, *grid.up)]

    def test_one_way_of_gridlines_is_not_a_grid(self) -> None:
        # Found on a real plan: letters along the top, and the lines that cross them
        # labelled on another sheet.
        builder = geometry.Builder(geometry.Method.PDF_VECTOR)
        for n, label in enumerate(["A", "B", "C", "D"]):
            gridline(builder, label, 100.0 + 84.0 * n, 20.0, 90.0, 500.0)

        assert detected(builder) is None

    def test_a_label_on_two_different_lines_is_trusted_on_neither(self) -> None:
        builder = square_plan()
        gridline(builder, "B", 600.0, 20.0, 90.0, 500.0)

        grid = detected(builder)

        assert grid is not None
        assert [line.label for line in grid.across] == ["A", "C", "D"]

    def test_a_gridline_with_a_bubble_at_each_end_is_one_line(self) -> None:
        builder = square_plan()
        for n, label in enumerate(["A", "B", "C", "D"]):
            bubble(builder, label, 100.0 + 84.0 * n, 540.0)

        grid = detected(builder)

        assert grid is not None
        assert [line.label for line in grid.across] == ["A", "B", "C", "D"]


@pytest.mark.req("FR-VIS-07")
class TestASkewedWing:
    def test_a_wing_s_gridlines_are_found_at_their_angle(self) -> None:
        builder = geometry.Builder(geometry.Method.PDF_VECTOR)
        wing(builder)

        grid = detected(builder)

        assert grid is not None
        # The numbers' lines stand nearer upright, so they are the lines "across".
        assert [line.label for line in grid.across] == ["1", "2", "3"]
        assert [line.label for line in grid.up] == ["K", "L", "M", "N"]
        lean = math.degrees(math.atan(grid.up[0].slope))
        assert lean == pytest.approx(-38.4, abs=0.05)
        assert math.degrees(math.atan(grid.across[0].slope)) == pytest.approx(38.4, abs=0.05)

    def test_a_point_in_the_wing_is_named_by_the_lines_either_side_of_it(self) -> None:
        builder = geometry.Builder(geometry.Method.PDF_VECTOR)
        wing(builder)
        grid = detected(builder)
        assert grid is not None
        k, one = grid.up[0], grid.across[0]

        # Where line K crosses line 1: on both, so it is the intersection.
        t = math.radians(38.4)
        # K: (30, 300) + s(cos t, -sin t); 1: (60, 200) + u(sin t, cos t).
        # Solved for s: the two are square to each other.
        s = (60.0 - 30.0) * math.cos(t) - (200.0 - 300.0) * math.sin(t)
        x, y = 30.0 + s * math.cos(t), 300.0 - s * math.sin(t)
        assert k.at(x) == pytest.approx(y, abs=0.05)
        assert one.at(y) == pytest.approx(x, abs=0.05)
        assert grid.reference(x, y) == "Grid 1K"
        # Half a bay along K towards 2, and half a bay towards L.
        u, v = 0.5 * (70.0 * math.cos(t) + 10.0 * math.sin(t)), 0.5 * 100.0 * math.cos(t)
        inside = (x + u * math.cos(t) + v * math.sin(t), y - u * math.sin(t) + v * math.cos(t))
        assert grid.reference(*inside) == f"Grid 1K{grids.BAY}2L"

    def test_beside_a_square_block_the_grid_is_the_two_ways_that_are_square_to_each_other(
        self,
    ) -> None:
        # Found on a real plan: the main block's letters along the top, whose crossing
        # lines are labelled on another sheet, and the wing with both its ways. The letters
        # along the top are not paired with the wing's lines: they are another grid.
        builder = geometry.Builder(geometry.Method.PDF_VECTOR)
        wing(builder)
        for n, label in enumerate(["AA", "AB", "AC", "AD", "AE"]):
            gridline(builder, label, 500.0 + 84.0 * n, 20.0, 90.0, 700.0)

        grid = detected(builder)

        assert grid is not None
        assert [line.label for line in grid.across] == ["1", "2", "3"]
        assert [line.label for line in grid.up] == ["K", "L", "M", "N"]
        # A point in the main block is past the ends of the wing's lines: it has no name
        # here, where before the fix a wing's lines were taken to run for ever.
        assert grid.reference(700.0, 400.0) is None

    def test_both_lettered_or_both_numbered_the_labels_are_parted_by_a_stroke(self) -> None:
        letters = grids.GridSystem(
            (grids.GridLine("AA", "x", 100.0), grids.GridLine("AB", "x", 184.0)),
            (grids.GridLine("K", "y", 100.0), grids.GridLine("L", "y", 184.0)),
        )

        assert letters.reference(100.0, 100.0) == "Grid AA/K"
        assert letters.reference(140.0, 140.0) == f"Grid AA/K{grids.BAY}AB/L"


def test_a_sheet_with_no_bubbles_has_no_grid() -> None:
    builder = geometry.Builder(geometry.Method.PDF_VECTOR)
    dashed(builder, (100.0, 20.0), 90.0, 500.0)

    assert grids.detect(builder.table()) is None
    assert isinstance(builder.table(), pa.Table)

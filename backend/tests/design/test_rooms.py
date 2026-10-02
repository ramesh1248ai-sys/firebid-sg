"""The spaces of a plan, from its linework (FR-DSN-02).

A 30 m x 20 m building at 1:100: an 80 m2 ward, a 12 m2 store, a crossed lift shaft, and the
open floor around them.
"""

from __future__ import annotations

import pytest

from firebid.design import rooms
from firebid.drawings import geometry

from .plans import DENOMINATOR, GREY, REGION, Plan, building

pytestmark = pytest.mark.req("FR-DSN-02")


def _named(found: rooms.Found, name: str) -> rooms.Space:
    return next(space for space in found.spaces if space.labels == [name])


def test_rooms_are_found_through_their_doorways_at_their_real_area() -> None:
    found = rooms.find(building().table(), REGION, DENOMINATOR)

    assert _named(found, "WARD A").area_m2 == pytest.approx(80.0, rel=0.03)
    assert _named(found, "STORE").area_m2 == pytest.approx(12.0, rel=0.05)
    # The open floor is one space: a doorway does not join it to the rooms.
    assert len(found.spaces) == 3


def test_the_spaces_account_for_the_building_less_its_shafts() -> None:
    found = rooms.find(building().table(), REGION, DENOMINATOR)

    assert len(found.shafts) == 1
    assert found.footprint_m2 == pytest.approx(600.0 - 9.0, rel=0.02)
    assert sum(space.area_m2 for space in found.spaces) == pytest.approx(591.0, rel=0.03)


def test_nothing_outside_the_building_is_a_space() -> None:
    plan = building()
    # A site boundary well clear of the building, and a kerb beside it.
    plan.wall(5.0, 295.0, 390.0, 295.0)
    plan.wall(5.0, 20.0, 5.0, 295.0)
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    for space in found.spaces:
        assert space.box[0] >= 49.0 and space.box[2] <= 351.0
        assert space.box[1] >= 49.0 and space.box[3] <= 251.0


def test_a_structural_grid_line_does_not_cut_a_room_in_two() -> None:
    plan = building()
    plan.grid_line(100.0, 10.0, 290.0)  # through the middle of the ward
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    assert _named(found, "WARD A").area_m2 == pytest.approx(80.0, rel=0.03)
    assert len(found.spaces) == 3


def test_the_services_in_colour_are_not_walls() -> None:
    plan = building()
    plan.pipe(60.0, 90.0, 140.0, 90.0)  # a main drawn across the ward
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    assert found.base_colours == [GREY]
    assert _named(found, "WARD A").area_m2 == pytest.approx(80.0, rel=0.03)


def test_pipe_tags_and_grid_references_are_not_names() -> None:
    plan = building()
    plan.name("Ø65 mm SPR", 100.0, 100.0)
    plan.name("A3", 100.0, 110.0)
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    assert _named(found, "WARD A").labels == ["WARD A"]


def test_a_part_that_is_not_plan_is_left_out() -> None:
    found = rooms.find(
        building().table(), REGION, DENOMINATOR, exclude=[(50.0, 50.0, 150.0, 130.0)]
    )

    assert all("WARD A" not in space.labels for space in found.spaces)


def test_a_sheet_keeps_to_its_side_of_a_match_line() -> None:
    left = [(0.0, 0.0), (200.0, 0.0), (200.0, 300.0), (0.0, 300.0)]
    found = rooms.find(building().table(), REGION, DENOMINATOR, scope=left)

    assert all(space.box[2] <= 201.0 for space in found.spaces)
    assert sum(space.area_m2 for space in found.spaces) == pytest.approx(300.0, rel=0.05)


def test_a_wing_built_at_an_angle_is_set_out_along_its_own_walls() -> None:
    plan = Plan()
    # A 20 m x 10 m room turned 30 degrees.
    corners = [(100.0, 100.0), (273.2, 200.0), (223.2, 286.6), (50.0, 186.6)]
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1], strict=True):
        plan.wall(x0, y0, x1, y1)
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    assert len(found.spaces) == 1
    assert found.spaces[0].angle_deg == pytest.approx(30.0, abs=1.0)
    assert found.spaces[0].area_m2 == pytest.approx(200.0, rel=0.03)


def test_a_sheet_with_no_base_plan_says_so() -> None:
    plan = Plan()
    plan.pipe(10.0, 10.0, 300.0, 10.0)
    found = rooms.find(plan.table(), REGION, DENOMINATOR)

    assert found.spaces == []
    assert found.note == "no base plan linework was found in the plan area"


def test_the_same_building_is_the_same_size_at_another_scale() -> None:
    # The same sheet read as 1:50 is a building half the size each way.
    found = rooms.find(building(door_mm=18.0).table(), REGION, 50.0)

    assert _named(found, "WARD A").area_m2 == pytest.approx(20.0, rel=0.05)


def test_an_empty_table_has_no_spaces() -> None:
    table = geometry.Builder(geometry.Method.PDF_VECTOR).table()

    assert rooms.find(table, REGION, DENOMINATOR).spaces == []

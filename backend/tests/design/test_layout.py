"""A proposed sprinkler layout: heads, range pipes, and what was left out (FR-DSN-03)."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.design import basis, layout, rooms
from firebid.design.layout import Criterion
from firebid.design.rooms import Space

from .plans import DENOMINATOR, REGION, building

pytestmark = pytest.mark.req("FR-DSN-03")

RULES = basis.load_rules()
OH = Criterion("oh", "Ordinary Hazard", 12.0, (4000, 3000), "test")
# Every space on the preferred grid, however small: for rows of a known length.
GRID = dataclasses.replace(RULES, grid_from_m2=0.0)


def rectangle(width_m: float, depth_m: float, *names: str, index: int = 0) -> Space:
    """A rectangular space at 1:100, its corner at (10, 10) on the sheet."""
    scale = 2.0
    mask = np.ones((round(depth_m * 10 * scale), round(width_m * 10 * scale)), dtype=bool)
    return Space(
        index=index,
        kind="room",
        area_m2=float(mask.sum()) / (10 * scale) ** 2,  # as the plan reader measures it
        box=(10.0, 10.0, 10.0 + width_m * 10, 10.0 + depth_m * 10),
        centre=(10.0 + width_m * 5, 10.0 + depth_m * 5),
        labels=list(names),
        mask=mask,
        origin=(10.0, 10.0),
        px_per_mm=scale,
    )


def test_an_open_floor_follows_the_preferred_grid() -> None:
    plan = layout.plan([rectangle(28.0, 14.0)], DENOMINATOR, OH, RULES)

    # 28 m / 2.8 m = 10 along, 14 m / 2.8 m = 5 across.
    assert plan.totals()["heads"] == 50
    assert plan.spaces[0].pitch_mm == (2800, 2800)


def test_no_wall_is_further_than_half_a_spacing_from_a_head() -> None:
    plan = layout.plan([rectangle(30.0, 15.0)], DENOMINATOR, OH, RULES)

    xs = sorted({round(head.x, 3) for head in plan.heads})
    ys = sorted({round(head.y, 3) for head in plan.heads})
    step_x, step_y = xs[1] - xs[0], ys[1] - ys[0]
    assert step_x * DENOMINATOR <= 2800 and step_y * DENOMINATOR <= 2800
    assert xs[0] - 10.0 == pytest.approx(step_x / 2, abs=0.01)
    assert 310.0 - xs[-1] == pytest.approx(step_x / 2, abs=0.01)
    assert ys[0] - 10.0 == pytest.approx(step_y / 2, abs=0.01)


def test_a_room_gets_the_fewest_heads_the_criterion_allows() -> None:
    # 4 m x 3 m is one head at 12 m2; 5 m x 3 m is two.
    one = layout.plan([rectangle(4.0, 3.0, "STORE")], DENOMINATOR, OH, RULES)
    two = layout.plan([rectangle(5.0, 3.0, "STORE")], DENOMINATOR, OH, RULES)
    # The wider spacing runs whichever way suits the room.
    turned = layout.plan([rectangle(3.0, 4.0, "STORE")], DENOMINATOR, OH, RULES)

    assert [p.totals()["heads"] for p in (one, two, turned)] == [1, 2, 1]


def test_the_stricter_of_the_grid_and_the_criterion_governs() -> None:
    tight = Criterion("hh", "High Hazard", 6.0, (2500, 2400), "test")

    assert layout.pitch(OH, RULES) == (2800, 2800)
    assert layout.pitch(tight, RULES) == (2500, 2400)
    # 2.8 m x 2.8 m is 7.84 m2: over a 7 m2 limit, so the spacing closes up until it is not.
    along, across = layout.pitch(Criterion("x", "x", 7.0, (4000, 4000), "test"), RULES)
    assert along * across <= 7_000_000 < (along + 50) * across


def test_every_space_has_a_head_however_small() -> None:
    plan = layout.plan([rectangle(1.2, 1.3, "CLEANER")], DENOMINATOR, OH, RULES)

    assert plan.totals()["heads"] == 1


def test_omitted_spaces_are_listed_with_the_rule_that_omitted_them() -> None:
    spaces = [
        rectangle(3.0, 3.0, "LIFT 4", index=0),
        rectangle(6.0, 3.0, "STAIR 01", index=1),
        rectangle(6.0, 3.0, "OFFICE", index=2),
    ]
    plan = layout.plan(spaces, DENOMINATOR, OH, RULES)

    assert [(s.name, s.omitted_by, s.heads) for s in plan.spaces] == [
        ("LIFT 4", r"\bLIFT\b", 0),
        ("STAIR 01", r"\bSTAIR", 0),
        ("OFFICE", None, 2),
    ]
    assert plan.totals()["omitted"] == 2
    assert {head.space for head in plan.heads} == {2}


def test_one_riser_does_not_empty_a_ward() -> None:
    plan = layout.plan([rectangle(10.0, 8.0, "WARD", "E-RISER")], DENOMINATOR, OH, RULES)

    assert plan.spaces[0].omitted_by is None
    assert plan.totals()["heads"] > 0


def test_a_space_with_no_name_is_sprinklered() -> None:
    plan = layout.plan([rectangle(6.0, 3.0)], DENOMINATOR, OH, RULES)

    assert plan.totals()["heads"] == 2


def test_head_type_and_rating_follow_the_space_and_the_level() -> None:
    office = layout.plan([rectangle(4.0, 3.0, "OFFICE")], DENOMINATOR, OH, RULES, level="L10")
    pumps = layout.plan([rectangle(4.0, 3.0, "PUMP ROOM")], DENOMINATOR, OH, RULES, level="L10")
    car_park = layout.plan([rectangle(4.0, 3.0, "LOBBY")], DENOMINATOR, OH, RULES, level="B1")
    mixed = layout.plan(
        [rectangle(4.0, 3.0, "OFFICE", "PUMP ROOM")], DENOMINATOR, OH, RULES, level="L10"
    )

    def kind(plan: layout.Layout) -> tuple[str, int]:
        return plan.heads[0].object_type, plan.heads[0].temperature_c

    assert kind(office) == ("sprinkler_pendent", 68)
    assert kind(pumps) == ("sprinkler_pendent", 93)
    assert kind(car_park) == ("sprinkler_upright", 68)
    assert kind(mixed) == ("sprinkler_pendent", 68)
    assert "FC05" in pumps.heads[0].rule_note


def test_a_range_pipe_is_sized_by_the_heads_each_length_feeds() -> None:
    # One row of five heads, 2.8 m apart, fed from a main 1 m beyond its left end.
    space = rectangle(14.0, 2.8)
    main = [[(0.0, 0.0), (0.0, 100.0)]]
    plan = layout.plan([space], DENOMINATOR, OH, GRID, pipes=main)

    (pipe,) = plan.ranges
    assert pipe.heads == 5
    # From the feed end: lengths carrying 4, 3, 2 and 1 heads.
    assert pipe.lengths_mm == {40: 2800, 32: 2800, 25: 5600}
    # The feed carries all five: DN50, over the 2.4 m from the first head to the main.
    assert (pipe.feed_dn, pipe.feed_mm, pipe.remote) == (50, 2400, False)
    assert plan.totals()["range_pipe_m"] == {25: 5.6, 32: 2.8, 40: 2.8, 50: 2.4}


def test_a_row_is_fed_from_the_end_nearer_the_main() -> None:
    space = rectangle(14.0, 2.8)
    right = [[(160.0, 0.0), (160.0, 100.0)]]
    plan = layout.plan([space], DENOMINATOR, OH, GRID, pipes=right)

    (pipe,) = plan.ranges
    assert pipe.points[0][0] > pipe.points[-1][0]
    assert pipe.feed_mm == 2400


def test_a_row_with_no_main_in_reach_is_fed_by_allowance_and_says_so() -> None:
    far = [[(1000.0, 0.0), (1000.0, 100.0)]]
    for pipes in (None, far):
        plan = layout.plan([rectangle(14.0, 2.8)], DENOMINATOR, OH, GRID, pipes=pipes)
        (pipe,) = plan.ranges
        assert pipe.remote
        assert (pipe.feed_dn, pipe.feed_mm) == (32, 5 * 6000)
        assert plan.totals()["remote_rows"] == 1


def test_a_range_pipe_does_not_run_through_a_wall() -> None:
    # A U-shaped space: two wings joined along the bottom, a wall's width apart at the top.
    space = rectangle(14.0, 8.4)
    mask = space.mask.copy()
    mask[: round(5.6 * 20), round(5.6 * 20) : round(8.4 * 20)] = False
    space = dataclasses.replace(space, mask=mask)
    plan = layout.plan([space], DENOMINATOR, OH, RULES)

    for pipe in plan.ranges:
        for (x0, y0), (x1, y1) in zip(pipe.points, pipe.points[1:], strict=False):
            assert space.contains((x0 + x1) / 2, (y0 + y1) / 2)
    assert all(space.contains(head.x, head.y) for head in plan.heads)


def test_a_layout_from_a_plan_keeps_to_its_floor() -> None:
    found = rooms.find(building().table(), REGION, DENOMINATOR)
    plan = layout.plan(found.spaces, DENOMINATOR, basis.default_criterion(), RULES, level="L1")

    by_index = {space.index: space for space in found.spaces}
    assert all(by_index[head.space].contains(head.x, head.y) for head in plan.heads)
    # 591 m2 of floor at no more than 12 m2 a head, and no closer than the grid.
    assert math.ceil(591 / 12) <= plan.totals()["heads"] <= 110
    # Nothing in the crossed shaft.
    assert not any(320.0 < head.x < 350.0 and 50.0 < head.y < 80.0 for head in plan.heads)


@settings(max_examples=60, deadline=None)
@given(
    width=st.floats(min_value=1.0, max_value=40.0),
    depth=st.floats(min_value=1.0, max_value=40.0),
)
def test_no_head_covers_more_than_the_criterion_allows(width: float, depth: float) -> None:
    space = rectangle(width, depth, "ROOM")
    plan = layout.plan([space], DENOMINATOR, OH, RULES)

    heads = plan.totals()["heads"]
    assert heads >= 1
    assert space.area_m2 / heads <= OH.max_area_m2 + 1e-6
    # Lengths are whole millimetres (guardrail 3).
    for pipe in plan.ranges:
        assert all(isinstance(v, int) for v in pipe.lengths_mm.values())
        assert isinstance(pipe.feed_mm, int)

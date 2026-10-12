"""A sheet's match lines and its side of them (FR-DSN-06)."""

from __future__ import annotations

import math

import pytest

from firebid.design import match_lines, rooms
from tests.design.plans import DENOMINATOR, REGION, Plan, building

pytestmark = pytest.mark.req("FR-DSN-06")

BLACK = 0x000000


def dashed(plan: Plan, x: float, y0: float, y1: float, colour: int = BLACK) -> None:
    """A match line as it is drawn: long dashes down the sheet."""
    y = y0
    while y < y1:
        plan.wall(x, y, x, min(y + 14.0, y1), colour)
        y += 20.0


def west_sheet() -> Plan:
    """The building, cut at x = 200 with the services drawn on the west side only."""
    plan = building()
    dashed(plan, 200.0, 20.0, 280.0)
    plan.name("MATCH LINE - SEE DWG FP-L10-102", 205.0, 30.0)
    plan.pipe(60.0, 100.0, 195.0, 100.0)
    plan.pipe(60.0, 200.0, 195.0, 200.0)
    plan.pipe(205.0, 100.0, 230.0, 100.0)  # a stub past the line, to show where it goes on
    return plan


def test_a_match_line_is_found_from_its_label_and_names_the_sheet_it_continues_on() -> None:
    found = match_lines.find(west_sheet().table(), REGION)

    assert found.unplaced == []
    assert len(found.lines) == 1
    line = found.lines[0]
    assert line.label == "MATCH LINE - SEE DWG FP-L10-102"
    assert line.other_sheet == "FP-L10-102"
    # Vertical at x = 200, across the whole plan area.
    assert line.a[0] == pytest.approx(200.0, abs=0.5)
    assert line.b[0] == pytest.approx(200.0, abs=0.5)
    assert {round(line.a[1]), round(line.b[1])} == {round(REGION[1]), round(REGION[3])}


def test_the_sheet_s_side_is_the_one_with_more_of_the_services_drawn() -> None:
    found = match_lines.find(west_sheet().table(), REGION)
    line = found.lines[0]

    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side)])

    assert line.reason == "the side with more of the services drawn"
    assert polygon is not None
    assert max(x for x, _ in polygon) == pytest.approx(200.0, abs=0.5)
    assert min(x for x, _ in polygon) == pytest.approx(REGION[0])


def test_the_other_side_is_the_rest_of_the_plan() -> None:
    line = match_lines.find(west_sheet().table(), REGION).lines[0]

    polygon = match_lines.scope(REGION, [(line.a, line.b, -line.side)])

    assert polygon is not None
    assert min(x for x, _ in polygon) == pytest.approx(200.0, abs=0.5)
    assert max(x for x, _ in polygon) == pytest.approx(REGION[2])


def test_with_no_services_drawn_the_larger_side_is_proposed_and_says_so() -> None:
    plan = building()
    dashed(plan, 120.0, 20.0, 280.0)
    plan.name("MATCH LINE", 125.0, 30.0)

    line = match_lines.find(plan.table(), REGION).lines[0]
    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side)])

    assert line.reason == "the larger side: no services are drawn on either"
    assert line.other_sheet is None
    assert polygon is not None
    assert min(x for x, _ in polygon) == pytest.approx(120.0, abs=0.5)


def test_a_sheet_in_the_middle_keeps_what_is_between_its_two_lines() -> None:
    plan = building()
    dashed(plan, 150.0, 20.0, 280.0)
    plan.name("MATCH LINE - SEE FP-101", 145.0, 30.0)
    dashed(plan, 250.0, 20.0, 280.0)
    plan.name("MATCH LINE - SEE FP-103", 255.0, 30.0)
    plan.pipe(155.0, 150.0, 245.0, 150.0)

    found = match_lines.find(plan.table(), REGION)
    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side) for line in found.lines])

    assert sorted(line.other_sheet or "" for line in found.lines) == ["FP-101", "FP-103"]
    assert polygon is not None
    assert min(x for x, _ in polygon) == pytest.approx(150.0, abs=0.5)
    assert max(x for x, _ in polygon) == pytest.approx(250.0, abs=0.5)


def test_two_labels_on_one_line_are_one_match_line() -> None:
    plan = west_sheet()
    plan.name("MATCH LINE - SEE DWG FP-L10-102", 205.0, 270.0)

    assert len(match_lines.find(plan.table(), REGION).lines) == 1


def test_a_label_with_no_line_beside_it_is_named_and_proposes_no_scope() -> None:
    plan = building()
    plan.name("MATCH LINE - SEE FP-L05-203", 200.0, 270.0)

    found = match_lines.find(plan.table(), REGION)

    assert found.lines == []
    assert found.unplaced == ["MATCH LINE - SEE FP-L05-203"]
    assert match_lines.scope(REGION, []) is None


def test_a_sheet_with_no_match_line_has_none() -> None:
    found = match_lines.find(building().table(), REGION)

    assert (found.lines, found.unplaced) == ([], [])


def test_a_short_line_beside_the_label_is_not_a_match_line() -> None:
    plan = building()
    plan.wall(200.0, 28.0, 200.0, 40.0, BLACK)  # a leader, not a line across the plan
    plan.name("MATCH LINE", 205.0, 30.0)

    found = match_lines.find(plan.table(), REGION)

    assert found.lines == []
    assert found.unplaced == ["MATCH LINE"]


def test_a_match_line_at_an_angle_is_followed() -> None:
    plan = building()
    plan.wall(100.0, 20.0, 300.0, 280.0, BLACK)
    plan.name("MATCH LINE", 205.0, 150.0)
    plan.pipe(60.0, 200.0, 150.0, 200.0)

    line = match_lines.find(plan.table(), REGION).lines[0]
    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side)])

    assert polygon is not None
    # The side with the pipe: the bottom-left corner is in, the top-right is out.
    corners = {(round(x), round(y)) for x, y in polygon}
    assert (round(REGION[0]), round(REGION[3])) in corners
    assert (round(REGION[2]), round(REGION[1])) not in corners


def test_the_spaces_found_keep_to_the_sheet_s_side() -> None:
    plan = west_sheet()
    line = match_lines.find(plan.table(), REGION).lines[0]
    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side)])

    whole = rooms.find(plan.table(), REGION, DENOMINATOR)
    limited = rooms.find(plan.table(), REGION, DENOMINATOR, scope=polygon)

    assert all(space.box[2] <= 201.0 for space in limited.spaces)
    # The building is 30 m x 20 m from x = 50 to 350: the west sheet answers for half.
    assert sum(space.area_m2 for space in limited.spaces) == pytest.approx(
        sum(space.area_m2 for space in whole.spaces) / 2, rel=0.08
    )


def test_the_stored_form_gives_the_same_scope_and_takes_a_person_s_side() -> None:
    line = match_lines.find(west_sheet().table(), REGION).lines[0]
    stored = line.to_json()

    proposed = match_lines.scope_from_json(REGION, [stored])
    other = match_lines.scope_from_json(REGION, [stored | {"side": -stored["side"]}])

    assert (stored["side"], stored["proposed_side"]) == (line.side, line.side)
    assert proposed is not None and other is not None
    assert max(x for x, _ in proposed) == pytest.approx(200.0, abs=0.5)
    assert min(x for x, _ in other) == pytest.approx(200.0, abs=0.5)


def test_pipe_runs_found_on_the_sheet_decide_the_side_before_colour_does() -> None:
    plan = building()
    dashed(plan, 200.0, 20.0, 280.0)
    plan.name("MATCH LINE", 205.0, 30.0)
    plan.pipe(60.0, 100.0, 195.0, 100.0)  # coloured linework on the west side

    by_colour = match_lines.find(plan.table(), REGION).lines[0]
    by_pipes = match_lines.find(
        plan.table(), REGION, services=[[(210.0, 150.0), (340.0, 150.0)]]
    ).lines[0]

    assert by_colour.reason == "the side with more of the services drawn"
    assert by_pipes.reason == "the side with more of the pipework drawn"
    assert by_pipes.side == -by_colour.side
    assert max(by_pipes.services_mm) == pytest.approx(130.0)


def test_a_grid_line_beside_the_label_is_not_taken_for_the_match_line() -> None:
    plan = building()
    dashed(plan, 200.0, 20.0, 280.0)
    plan.wall(20.0, 36.0, 380.0, 36.0, BLACK)  # a grid line, longer and as near to the words
    plan.name("MATCH LINE - SEE FP-102", 226.0, 32.0)

    blind = match_lines.find(plan.table(), REGION).lines[0]
    knowing = match_lines.find(plan.table(), REGION, grid=[("y", 36.0)]).lines[0]

    assert blind.a[1] == pytest.approx(36.0, abs=0.5)  # the longer of two lines equally near
    assert knowing.a[0] == pytest.approx(200.0, abs=0.5)


def test_a_floor_cut_on_a_grid_line_is_still_found() -> None:
    plan = building()
    dashed(plan, 200.0, 20.0, 280.0)
    plan.name("MATCH LINE", 205.0, 30.0)

    line = match_lines.find(plan.table(), REGION, grid=[("x", 200.0)]).lines[0]

    assert line.a[0] == pytest.approx(200.0, abs=0.5)


def upright(plan: Plan, text: str, x: float, y0: float, y1: float) -> None:
    """Words written up the sheet, as a note along a vertical line is."""
    plan.builder.text(
        text, (x - 3.0, y0, x + 3.0, y1), plan.builder.group(), height=6.0, rotation=90.0
    )


def short_dashes(plan: Plan, x: float, y0: float, y1: float) -> None:
    y = y0
    while y < y1:
        plan.wall(x, y, x, min(y + 3.0, y1), BLACK)
        y += 4.5


def dense_sheet() -> Plan:
    """As a real plan is: a wall, a pipe and a room name all pass the note, the cut is a
    row of short dashes beside it, and the sheet it continues on is a number written
    after the note, not in it."""
    plan = building()
    short_dashes(plan, 200.0, 20.0, 280.0)
    upright(plan, "FOR CONTINUATION, REFER TO DRG. NO. -", 195.0, 120.0, 240.0)
    upright(plan, "60743399_ACM_TN_TO_D_MFP_A03-10-02", 195.0, 20.0, 116.0)
    plan.wall(60.0, 180.0, 340.0, 180.0)  # a long wall straight through the note
    plan.wall(192.0, 60.0, 192.0, 240.0)  # a solid wall alongside it, nearer than the cut
    plan.pipe(210.0, 100.0, 340.0, 100.0)
    plan.pipe(210.0, 200.0, 340.0, 200.0)
    plan.pipe(150.0, 100.0, 190.0, 100.0)
    return plan


def test_a_continuation_note_marks_a_match_line_as_the_words_match_line_do() -> None:
    found = match_lines.find(dense_sheet().table(), REGION)

    assert found.unplaced == []
    (line,) = found.lines
    assert line.label == "FOR CONTINUATION, REFER TO DRG. NO. -"
    assert line.a[0] == pytest.approx(200.0, abs=0.5) and line.b[0] == pytest.approx(200.0, abs=0.5)


def test_the_sheet_it_continues_on_is_read_from_the_number_written_beside_the_note() -> None:
    (line,) = match_lines.find(dense_sheet().table(), REGION).lines

    assert line.other_sheet == "60743399_ACM_TN_TO_D_MFP_A03-10-02"


def test_the_dashed_line_along_the_note_is_taken_not_the_wall_through_it_or_beside_it() -> None:
    (line,) = match_lines.find(dense_sheet().table(), REGION).lines
    polygon = match_lines.scope(REGION, [(line.a, line.b, line.side)])

    # Not the wall at y = 180 that crosses the words, nor the solid wall at x = 192.
    assert {round(line.a[0]), round(line.b[0])} == {200}
    assert polygon is not None
    # The side with more of the services: east of the cut.
    assert min(x for x, _ in polygon) == pytest.approx(200.0, abs=0.5)


def lettered(
    plan: Plan, text: str, x: float, y: float, rotation: float, height: float = 7.0
) -> None:
    """Words as some CAD exports write them at an angle: a letter a span, from (x, y)."""
    turn = math.radians(rotation)
    wide, tall = 0.5 * height, 0.7 * height
    half_x = (wide * abs(math.cos(turn)) + tall * abs(math.sin(turn))) / 2
    half_y = (wide * abs(math.sin(turn)) + tall * abs(math.cos(turn))) / 2
    for index, letter in enumerate(text):
        if letter == " ":
            continue
        along = index * 0.6 * height
        centre_x, centre_y = x + along * math.cos(turn), y - along * math.sin(turn)
        plan.builder.text(
            letter,
            (centre_x - half_x, centre_y - half_y, centre_x + half_x, centre_y + half_y),
            plan.builder.group(),
            height=height,
            rotation=rotation,
        )


def slanted(plan: Plan, x: float, y: float, rotation: float, length: float) -> None:
    """A row of dashes from (x, y), at an angle measured up the sheet."""
    turn = math.radians(rotation)
    along = 0.0
    while along < length:
        end = min(along + 14.0, length)
        plan.wall(
            x + along * math.cos(turn),
            y - along * math.sin(turn),
            x + end * math.cos(turn),
            y - end * math.sin(turn),
            BLACK,
        )
        along += 20.0


WING_DEG = 51.6
NOTE_ON_THE_WING = "FOR CONTINUATION, REFER TO DRG. NO. - 60743399_ACM_TN_PO_D_MFP_A03-02-01"


def wing_sheet() -> Plan:
    """Found on a real tender: a wing at an angle to the sheet, cut along its own direction,
    with the note written along the cut a letter at a time and the sheet's number in it."""
    plan = building()
    slanted(plan, 120.0, 280.0, WING_DEG, 330.0)
    turn = math.radians(WING_DEG)
    # Beside the cut, 5 mm off it, starting 40 mm along.
    start_x = 120.0 + 40.0 * math.cos(turn) - 5.0 * math.sin(turn)
    start_y = 280.0 - 40.0 * math.sin(turn) - 5.0 * math.cos(turn)
    lettered(plan, NOTE_ON_THE_WING, start_x, start_y, WING_DEG)
    # Another row of dashes straight through the note, leaning the other way.
    slanted(plan, 80.0, 20.0, -WING_DEG, 330.0)
    return plan


def test_a_note_written_at_an_angle_a_letter_at_a_time_is_read_as_what_it_says() -> None:
    found = match_lines.find(wing_sheet().table(), REGION)

    assert found.unplaced == []
    (line,) = found.lines
    assert line.label == NOTE_ON_THE_WING
    assert line.other_sheet == "60743399_ACM_TN_PO_D_MFP_A03-02-01"


def test_its_line_is_the_one_along_the_words_not_the_one_leaning_the_other_way() -> None:
    (line,) = match_lines.find(wing_sheet().table(), REGION).lines

    # The cut climbs the sheet to the right, as the words do.
    (low_x, low_y), (high_x, high_y) = sorted([line.a, line.b])
    assert high_y < low_y
    assert math.degrees(math.atan2(low_y - high_y, high_x - low_x)) == pytest.approx(
        WING_DEG, abs=0.5
    )


def test_two_notes_on_one_line_that_name_two_sheets_are_one_line_that_goes_on_to_both() -> None:
    plan = west_sheet()
    plan.name("MATCH LINE - SEE DWG FP-L10-104", 205.0, 270.0)

    (line,) = match_lines.find(plan.table(), REGION).lines

    assert line.other_sheet == "FP-L10-102"
    assert line.other_sheets == ("FP-L10-102", "FP-L10-104")
    assert line.to_json()["other_sheets"] == ["FP-L10-102", "FP-L10-104"]

"""One sheet's scale checked against another's, by the grid they share (FR-VIS-05).

Found on a real tender of 148 sheets: sixty upper-floor plans state 1:100 and carry no
dimension at all, so each needed a person to calibrate it. They are drawn on the gridlines
of the floors below, whose dimensions prove 1:100 and so prove how far apart the gridlines
are. The gridlines are dashed, so they are found by their bubbles, which stand in a row.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from firebid.drawings import geometry, grids, scale, views
from firebid.drawings.scale import Evidence, ScaleStatus, Stated, Verdict
from firebid.evals import synthetic
from firebid.parsing.geometry_dxf import extract as extract_dxf

Bubble = tuple[str, float, float, float]
NOTHING = pa.table({})
STATED = Stated(100.0, False, "1 : 100")


def row(labels: str, start: float, step: float, y: float, radius: float = 5.0) -> list[Bubble]:
    return [(label, start + step * n, y, radius) for n, label in enumerate(labels.split())]


def column(labels: str, x: float, start: float, step: float) -> list[Bubble]:
    return [(label, x, start + step * n, 5.0) for n, label in enumerate(labels.split())]


def marked(labels: str, start: float, step: float) -> list[list[object]]:
    """A row of marks as it is stored: each label and its place along the row."""
    return [[label, round(start + step * n, 3)] for n, label in enumerate(labels.split())]


def unverified(stated: Stated = STATED, evidence: tuple[Evidence, ...] = ()) -> Verdict:
    return scale.verify(stated, list(evidence))


@pytest.mark.req("FR-VIS-05")
class TestGridMarks:
    def test_bubbles_in_a_row_mark_gridlines_whatever_is_drawn_through_them(self) -> None:
        bubbles = row("AC AD AE AF", 202.0, 84.0, 27.35) + column("K L M", 28.0, 255.9, 109.7)

        found = grids.marks(NOTHING, bubbles)

        assert found["across"] == [marked("AC AD AE AF", 202.0, 84.0)]
        assert found["up"] == [marked("K L M", 255.9, 109.7)]

    def test_a_symbol_drawn_as_a_letter_in_a_circle_is_not_a_gridline(self) -> None:
        # A sprinkler is an S in a small circle, and a row of them is common.
        sprinklers = [("S", 100.0 + 30 * n, 400.0, 2.5) for n in range(6)]

        assert grids.marks(NOTHING, sprinklers) == {"across": [], "up": []}

    def test_a_row_needs_three_bubbles_of_one_size_in_order_and_of_one_kind(self) -> None:
        two = row("A B", 100.0, 84.0, 20.0)
        out_of_order = row("FS DU FM", 100.0, 84.0, 60.0)
        mixed = row("A 2 C", 100.0, 84.0, 100.0)
        sizes = [("A", 100.0, 140.0, 5.0), ("B", 184.0, 140.0, 2.5), ("C", 268.0, 140.0, 9.0)]

        found = grids.marks(NOTHING, two + out_of_order + mixed + sizes)

        assert found["across"] == []

    def test_a_numbered_bubble_standing_on_a_row_of_letters_is_not_part_of_it(self) -> None:
        # Found on a real plan: gridlines K to O down the left edge, and the bubble of a
        # skewed gridline 4 a millimetre off their line.
        letters = column("K L M N O", 28.03, 255.9, 109.0)
        stray = [("4", 26.96, 451.1, 4.98)]

        (found,) = grids.marks(NOTHING, letters + stray)["up"]

        assert [label for label, _ in found] == list("KLMNO")

    def test_each_row_of_bubbles_is_kept_apart(self) -> None:
        # Found on a real plan: a skewed wing's gridlines have a row of bubbles of their own,
        # and a spacing from a bubble of one row to a bubble of the other means nothing.
        square, skewed = row("AC AD AE", 100.0, 84.0, 20.0), row("P Q R", 130.0, 66.0, 700.0)

        found = grids.marks(NOTHING, square + skewed)["across"]

        assert found == [marked("AC AD AE", 100.0, 84.0), marked("P Q R", 130.0, 66.0)]

    def test_a_gridline_with_a_bubble_at_each_end_is_one_mark(self) -> None:
        top, bottom = row("A B C", 100.0, 84.0, 20.0), row("A B C", 100.0, 84.0, 800.0)

        assert grids.marks(NOTHING, top + bottom)["across"] == [marked("A B C", 100.0, 84.0)]

    def test_a_label_at_two_places_marks_nothing(self) -> None:
        # Two plans side by side on one sheet, each with its own row of bubbles.
        left, right = row("A B C", 100.0, 84.0, 20.0), row("A B C", 600.0, 84.0, 700.0)

        assert grids.marks(NOTHING, left + right)["across"] == []

    def test_a_view_is_given_the_marks_of_the_gridlines_that_cross_it(self) -> None:
        found = grids.marks(NOTHING, row("A B C D", 100.0, 84.0, 20.0))

        inside = grids.marks_in(found, (150.0, 0.0, 300.0, 500.0))
        beside = grids.marks_in(found, (150.0, 0.0, 200.0, 500.0))

        assert inside == {"across": [[["B", 184.0], ["C", 268.0]]], "up": []}
        assert beside == {"across": [], "up": []}, "one gridline has no spacing"

    def test_a_plan_read_from_a_drawing_is_given_its_marks(self) -> None:
        result = extract_dxf(synthetic.dxf_bytes(synthetic.general_arrangement()[0]), None)
        table = geometry.from_parquet(result["parquet"])
        (plan,) = views.analyse(table, tuple(result["page"]), "1:100", result.get("views", []))

        assert plan.marks is not None
        assert len(plan.marks["across"][0]) >= 3 and len(plan.marks["up"][0]) >= 3


MARKS: scale.Marks = {"across": [marked("AC AD AE AF AG", 202.0, 84.0)]}
# The floor above shows the same gridlines somewhere else on its sheet.
ABOVE: scale.Marks = {"across": [marked("AC AD AE AF", 345.72, 84.0)]}
# A skewed wing: its bubbles stand where there was room, so their spacing says nothing.
SKEWED = marked("N O P Q", 255.9, 107.0)
KNOWN_SKEWED = marked("N O P Q", 300.0, 84.0)


@pytest.mark.req("FR-VIS-05")
class TestScaleFromASharedGrid:
    def known(self, marks: scale.Marks = MARKS) -> dict[tuple[str, str], scale.Spacing]:
        return scale.known_spacings([("A03-06-01", 100.0, marks)])

    def test_a_proved_view_makes_its_grid_spacings_known(self) -> None:
        known = self.known()

        assert known[("AC", "AD")] == scale.Spacing(8400.0, "A03-06-01")
        assert known[("AC", "AG")].mm == pytest.approx(33_600.0)

    def test_a_spacing_is_only_known_along_one_row(self) -> None:
        known = self.known({"across": [*MARKS["across"], KNOWN_SKEWED]})

        assert ("N", "O") in known and ("AC", "AD") in known
        assert ("AC", "N") not in known and ("AG", "N") not in known

    def test_a_stated_scale_the_grid_agrees_with_is_verified_and_says_from_which_sheet(
        self,
    ) -> None:
        grid = scale.grid_evidence(ABOVE, self.known())

        verdict = scale.corroborate(unverified(), grid)

        assert verdict is not None
        assert (verdict.status, verdict.denominator) == (ScaleStatus.VERIFIED, 100.0)
        assert verdict.measurable
        assert verdict.reason == "3 grid spacing(s) known from A03-06-01 agree with 1:100"
        assert [item.source for item in verdict.evidence] == ["grid"] * 3
        assert verdict.evidence[0].note == "gridlines AC and AD are 8400 mm apart on A03-06-01"

    def test_fewer_than_three_known_spacings_in_a_row_prove_nothing(self) -> None:
        few: scale.Marks = {"across": [ABOVE["across"][0][:3]]}
        # Two here and two there are not three in a row.
        split: scale.Marks = {"across": [ABOVE["across"][0][:3]], "up": [ABOVE["across"][0][1:]]}

        assert scale.corroborate(unverified(), scale.grid_evidence(few, self.known())) is None
        assert scale.corroborate(unverified(), scale.grid_evidence(split, self.known())) is None

    def test_a_row_must_agree_in_every_spacing(self) -> None:
        odd: scale.Marks = {
            "across": [[["AC", 100.0], ["AD", 184.0], ["AE", 268.0], ["AF", 300.0]]]
        }

        assert scale.corroborate(unverified(), scale.grid_evidence(odd, self.known())) is None

    def test_a_plan_at_another_scale_than_it_states_is_left_unverified(self) -> None:
        # Drawn at 1:200: the same gridlines, half as far apart on paper. The grid never
        # says "conflicting": a skewed grid's bubbles would say it of a sound sheet.
        half: scale.Marks = {"across": [marked("AC AD AE AF", 100.0, 42.0)]}

        assert scale.corroborate(unverified(), scale.grid_evidence(half, self.known())) is None

    def test_a_skewed_wing_s_bubbles_are_set_aside_and_said_to_be(self) -> None:
        # Found on a real tender: seven spacings of the square grid agreed with 1:100 and
        # the sheet stayed unverified, because two between a skewed wing's bubbles did not.
        known = self.known({"across": [*MARKS["across"]], "up": [KNOWN_SKEWED]})
        above: scale.Marks = {"across": ABOVE["across"], "up": [SKEWED]}

        verdict = scale.corroborate(unverified(), scale.grid_evidence(above, known))

        assert verdict is not None
        assert (verdict.status, verdict.denominator) == (ScaleStatus.VERIFIED, 100.0)
        assert verdict.reason == (
            "3 grid spacing(s) known from A03-06-01 agree with 1:100; 3 along another row of "
            "bubbles do not and were set aside (a skewed grid's bubbles are not square to it)"
        )
        assert len(verdict.evidence) == 6  # all of it is kept for a person to see

    def test_two_proved_views_that_disagree_about_a_spacing_prove_nothing_of_it(self) -> None:
        # Two buildings in one tender, their gridlines named alike and spaced differently.
        other: scale.Marks = {"across": [marked("AC AD AE AF", 100.0, 60.0)]}

        known = scale.known_spacings([("A03-06-01", 100.0, MARKS), ("B01-02-01", 100.0, other)])

        assert ("AC", "AD") not in known and ("AF", "AG") in known
        assert scale.corroborate(unverified(), scale.grid_evidence(ABOVE, known)) is None

    def test_only_an_unverified_view_is_decided_by_the_grid(self) -> None:
        grid = scale.grid_evidence(ABOVE, self.known())
        not_to_scale = scale.verify(Stated(None, True, "NTS"), [])
        conflicting = scale.verify(STATED, [Evidence(8400.0, 42.0, "figure")])

        assert not_to_scale.status is ScaleStatus.NTS
        assert conflicting.status is ScaleStatus.CONFLICTING
        assert scale.corroborate(not_to_scale, grid) is None
        assert scale.corroborate(conflicting, grid) is None

    def test_a_view_stating_no_scale_needs_its_own_dimensions_and_the_grid_to_agree(
        self,
    ) -> None:
        # A basement plan whose title block says "AS INDICATED".
        unstated = Stated(None, False, "AS INDICATED")
        dimensions = tuple(Evidence(8400.0, 84.0, "figure") for _ in range(13))
        grid = scale.grid_evidence(ABOVE, self.known())

        verdict = scale.corroborate(unverified(unstated, dimensions), grid)
        too_few = scale.corroborate(unverified(unstated, dimensions[:4]), grid)
        no_grid = scale.corroborate(unverified(unstated, dimensions), [])
        elsewhere = scale.corroborate(
            unverified(unstated, tuple(Evidence(8400.0, 42.0, "figure") for _ in range(13))), grid
        )

        assert verdict is not None
        assert (verdict.status, verdict.denominator) == (ScaleStatus.VERIFIED, 100.0)
        assert verdict.reason == (
            "no scale is stated; 13 dimension(s) on the view and 3 grid spacing(s) known "
            "from A03-06-01 all give 1:100"
        )
        assert len(verdict.evidence) == 16
        assert too_few is None and no_grid is None
        assert elsewhere is None, "its dimensions give 1:200 and the grid 1:100"

    def test_a_verdict_is_read_back_as_it_was_stored(self) -> None:
        verdict = scale.corroborate(unverified(), scale.grid_evidence(ABOVE, self.known()))
        assert verdict is not None

        again = Verdict.from_json(verdict.as_json())

        assert again.as_json() == verdict.as_json()
        assert (again.status, again.denominator, again.stated) == (
            verdict.status,
            verdict.denominator,
            verdict.stated,
        )

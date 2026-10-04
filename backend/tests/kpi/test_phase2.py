"""The Phase 2 KPIs as arithmetic (requirements §14; P2-09)."""

from __future__ import annotations

from datetime import date

import pytest

from firebid.kpi import phase2

pytestmark = pytest.mark.req("NFR-14")

MONDAY = date(2026, 9, 21)


class TestWorkingDays:
    def test_a_fortnight_is_ten_working_days(self) -> None:
        assert phase2.working_days(MONDAY, date(2026, 10, 5)) == 10

    def test_a_weekend_is_not_counted(self) -> None:
        friday = date(2026, 9, 25)

        assert phase2.working_days(friday, date(2026, 9, 28)) == 1
        assert phase2.working_days(friday, date(2026, 9, 27)) == 0

    def test_a_holiday_is_not_counted(self) -> None:
        holidays = frozenset({date(2026, 9, 23)})

        assert phase2.working_days(MONDAY, date(2026, 9, 25), holidays) == 3

    def test_ready_the_day_it_was_received_is_no_days(self) -> None:
        assert phase2.working_days(MONDAY, MONDAY) == 0
        assert phase2.working_days(MONDAY, date(2026, 9, 18)) == 0


class TestEditRatio:
    DRAFT = "Please confirm whether the specification or the drawing governs the pipe material."

    def test_issued_as_drafted_is_no_edit(self) -> None:
        assert phase2.edit_ratio(self.DRAFT, self.DRAFT) == 0.0
        assert phase2.edit_ratio(self.DRAFT, "  " + self.DRAFT.upper() + "\n") == 0.0

    def test_one_word_changed_is_that_share_of_the_draft(self) -> None:
        issued = self.DRAFT.replace("governs", "applies to")

        # Twelve words; one replaced and one added.
        assert phase2.edit_ratio(self.DRAFT, issued) == pytest.approx(2 / 13)
        assert phase2.minor_edits(self.DRAFT, issued, 0.20)

    def test_a_rewrite_is_not_a_minor_edit(self) -> None:
        issued = "Which pipe schedule applies at basement level one, and who supplies the valves?"

        assert phase2.edit_ratio(self.DRAFT, issued) > 0.8
        assert not phase2.minor_edits(self.DRAFT, issued, 0.20)

    def test_a_draft_thrown_away_is_wholly_edited(self) -> None:
        assert phase2.edit_ratio(self.DRAFT, "") == 1.0
        assert phase2.edit_ratio("", "") == 0.0

    def test_the_threshold_is_inclusive(self) -> None:
        assert phase2.minor_edits("a b c d e", "a b c d x", 0.20)
        assert not phase2.minor_edits("a b c d e", "a b c x y", 0.20)


class TestProvenance:
    def test_only_priced_lines_are_counted(self) -> None:
        share = phase2.provenance([(True, True), (True, False), (False, False), (True, True)])

        assert (share.count, share.of) == (2, 3)
        assert share.value == pytest.approx(2 / 3)

    def test_nothing_priced_is_not_measured_rather_than_zero(self) -> None:
        assert phase2.provenance([(False, False)]).value is None


class TestReduction:
    def test_a_measure_under_its_baseline_is_a_reduction(self) -> None:
        assert phase2.reduction(20, 13) == pytest.approx(0.35)
        assert phase2.reduction(20, 24) == pytest.approx(-0.2)

    def test_with_no_baseline_or_no_measure_there_is_nothing_to_say(self) -> None:
        assert phase2.reduction(None, 13) is None
        assert phase2.reduction(20, None) is None

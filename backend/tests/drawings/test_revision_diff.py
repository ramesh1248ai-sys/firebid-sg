"""Comparing two revisions of a sheet (FR-DOC-08).

The synthetic general arrangement at R01 and at R02, whose changes are seeded and known
exactly; and small hand-made sheets for each way of aligning two revisions.
"""

from __future__ import annotations

import itertools
import math

import pytest

from firebid.drawings import revision_diff as rd
from firebid.drawings.revision_diff import Element
from firebid.evals import synthetic_revision as fixture
from firebid.evals.qto_pipeline import Sheet, revision_of

pytestmark = pytest.mark.req("FR-DOC-08")


def compare(**revised: object) -> rd.Diff:
    old = revision_of(
        Sheet(fixture.NUMBER, fixture.sheet("R01", with_grid=bool(revised.get("with_grid", True))))
    )
    new = revision_of(Sheet(fixture.NUMBER, fixture.sheet("R02", revised=True, **revised)))  # type: ignore[arg-type]
    return rd.diff(
        old["elements"],
        new["elements"],
        old_grid=old["grid"],
        new_grid=new["grid"],
        old_frame=old["frame"],
        new_frame=new["frame"],
    )


def summary(diff: rd.Diff) -> list[tuple[str, str, str, object, object]]:
    return sorted(
        (
            c.change,
            c.kind,
            c.object_type,
            str(c.before if c.change == "changed" else None),
            str(c.after if c.change == "changed" else None),
        )
        for c in diff.changes
    )


SEEDED = sorted(
    (s.change, s.kind, s.object_type, str(s.before), str(s.after)) for s in fixture.SEEDED
)


class TestTheSeededRevision:
    def test_exactly_the_seeded_additions_removals_and_changes_are_reported(self) -> None:
        diff = compare()

        assert summary(diff) == SEEDED
        counts = diff.counts()
        assert (counts["added"], counts["removed"], counts["changed"]) == (1, 1, 2)
        # Everything else on the sheet: one riser, two valves, a reducer, 24 heads less the
        # one removed and the one retyped, and 16 pipe runs less the one resized.
        assert counts["unchanged"] == 4 + 22 + 15
        assert diff.alignment.method == "grid"

    def test_each_change_is_located_on_the_newer_sheet(self) -> None:
        diff = compare()
        at = {(c.change, c.kind): (c.x, c.y) for c in diff.changes if c.kind == "object"}

        # Paper millimetres at 1:100: the seeded positions, a hundredth of their distance
        # (a sidewall head's mark is centred half a millimetre off its pipe).
        added, removed = at[("added", "object")], at[("removed", "object")]
        expected = math.dist(fixture.ADDED_HEAD, fixture.REMOVED_HEAD) / 100
        assert math.dist(added, removed) == pytest.approx(expected, abs=0.6)

    def test_a_revision_compared_with_itself_has_no_changes(self) -> None:
        sheet = revision_of(Sheet(fixture.NUMBER, fixture.sheet("R01")))

        diff = rd.diff(
            sheet["elements"], sheet["elements"], old_frame=sheet["frame"], new_frame=sheet["frame"]
        )

        assert diff.changes == [] and diff.unchanged == len(sheet["elements"])

    def test_a_plan_moved_on_its_sheet_is_aligned_by_its_grid(self) -> None:
        diff = compare(shift=(600.0, 400.0))

        assert diff.alignment.method == "grid"
        # 6 mm right and 4 mm up on paper; sheet millimetres count down the page.
        assert diff.alignment.offset == pytest.approx((6.0, -4.0), abs=0.05)
        assert summary(diff) == SEEDED

    def test_with_no_grid_the_frames_align_the_revisions(self) -> None:
        diff = compare(with_grid=False)

        assert diff.alignment.method == "frame"
        assert summary(diff) == SEEDED


def head(ident: str, x: float, y: float, kind: str = "sprinkler_pendent") -> Element:
    return Element(ident, "object", kind, x, y)


def run(ident: str, dn: int, *points: tuple[float, float]) -> Element:
    length = sum(math.dist(a, b) for a, b in itertools.pairwise(points)) * 100
    return Element(
        ident, "run", "pipe_branch", points[0][0], points[0][1], {"dn": dn}, points, length
    )


OLD = [head(f"o{i}", 10.0 + 30 * i, 20.0) for i in range(4)]


class TestAlignment:
    def test_grid_lines_labelled_alike_give_scale_and_offset(self) -> None:
        old_grid = {"across": {"A": 10.0, "B": 70.0}, "up": {"1": 20.0, "2": 80.0}}
        new_grid = {"across": {"A": 25.0, "B": 55.0, "C": 85.0}, "up": {"1": 40.0, "2": 70.0}}
        new = [head(f"n{i}", 25.0 + 15 * i, 40.0) for i in range(4)]

        found = rd.align(OLD, new, old_grid=old_grid, new_grid=new_grid)

        assert found.method == "grid" and found.scale == pytest.approx((0.5, 0.5))
        assert found.apply((10.0, 20.0)) == pytest.approx((25.0, 40.0))
        assert found.matched == 1.0

    def test_a_grid_that_misleads_gives_way_to_the_frame(self) -> None:
        # The grids are labelled alike but the plan was not moved with them.
        misleading = {"across": {"A": 0.0, "B": 10.0}, "up": {"1": 0.0, "2": 10.0}}
        moved = {"across": {"A": 50.0, "B": 60.0}, "up": {"1": 50.0, "2": 60.0}}
        frame = (0.0, 0.0, 420.0, 297.0)

        found = rd.align(
            OLD, OLD, old_grid=misleading, new_grid=moved, old_frame=frame, new_frame=frame
        )

        assert found.method == "frame" and found.matched == 1.0

    def test_with_neither_the_offset_most_symbols_agree_on_is_fitted(self) -> None:
        new = [head(f"n{i}", e.x + 12.5, e.y - 4.0) for i, e in enumerate(OLD)]
        frame, smaller = (0.0, 0.0, 420.0, 297.0), (0.0, 0.0, 297.0, 210.0)

        found = rd.align(OLD, new, old_frame=frame, new_frame=smaller)

        assert found.method == "fit" and found.offset == pytest.approx((12.5, -4.0))

    def test_sheets_with_nothing_to_align_by_are_compared_as_they_are(self) -> None:
        assert rd.align([], []).method == "identity"


class TestTheDiff:
    def test_a_symbol_of_another_type_at_the_same_place_is_a_change(self) -> None:
        diff = rd.diff([head("a", 10, 10, "sprinkler_upright")], [head("b", 10.2, 10)])

        [change] = diff.changes
        assert (change.change, change.old_id, change.new_id) == ("changed", "a", "b")
        assert change.before == {"object_type": "sprinkler_upright"}
        assert change.after == {"object_type": "sprinkler_pendent"}

    def test_a_moved_symbol_is_a_removal_and_an_addition(self) -> None:
        diff = rd.diff([head("a", 10, 10)], [head("b", 40, 10)])

        assert [c.change for c in diff.changes] == ["added", "removed"]

    def test_an_attribute_that_changed_is_reported_with_before_and_after(self) -> None:
        old = [Element("a", "object", "fire_pump", 5, 5, {"tag": "FP-01"})]
        new = [Element("b", "object", "fire_pump", 5, 5, {"tag": "FP-03"})]

        [change] = rd.diff(old, new).changes

        assert (change.before, change.after) == ({"tag": "FP-01"}, {"tag": "FP-03"})

    def test_a_run_resized_or_extended_is_a_change_not_a_new_run(self) -> None:
        old = [run("a", 50, (10, 10), (10, 70)), run("b", 100, (0, 10), (60, 10))]
        new = [run("c", 65, (10, 10), (10, 70)), run("d", 100, (0, 10), (90, 10))]

        changes = {c.old_id: c for c in rd.diff(old, new).changes}

        assert changes["a"].before == {"dn": 50} and changes["a"].after == {"dn": 65}
        assert changes["b"].before == {"length_mm": 6000}
        assert changes["b"].after == {"length_mm": 9000}

    def test_a_removed_run_is_shown_where_it_was_on_the_newer_sheet(self) -> None:
        grid_old = {"across": {"A": 0.0, "B": 100.0}, "up": {"1": 0.0, "2": 100.0}}
        grid_new = {"across": {"A": 10.0, "B": 110.0}, "up": {"1": 5.0, "2": 105.0}}
        old = [head("h", 50, 50), run("a", 50, (10, 10), (10, 70))]
        new = [head("n", 60, 55)]

        diff = rd.diff(old, new, old_grid=grid_old, new_grid=grid_new)

        [removed] = diff.changes
        assert removed.change == "removed" and removed.points == ((20.0, 15.0), (20.0, 75.0))

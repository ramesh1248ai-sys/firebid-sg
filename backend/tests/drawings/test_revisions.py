"""Ordering revisions and reading them from filenames (FR-DOC-04).

The property tests are the point here: whatever order revisions arrive in, the latest one by
the scheme is the one chosen, and a superseded label is never chosen over a later one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from firebid.drawings.revisions import (
    Candidate,
    Scheme,
    compare,
    latest,
    parse,
    reconcile,
    revision_from_filename,
    scheme_named,
    schemes,
)

pytestmark = pytest.mark.req("FR-DOC-04")

DEFAULT = scheme_named(None)
TENDER_THEN_CONSTRUCTION = scheme_named("tender-then-construction")


class TestParsing:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("T2", ("T", 2)),
            ("P01", ("P", 1)),
            ("03", ("#", 3)),
            ("A", ("A-Z", 1)),
            ("Z", ("A-Z", 26)),
            ("AA", ("A-Z", 27)),
            ("r04", ("R", 4)),
        ],
    )
    def test_labels(self, label: str, expected: tuple[str, int]) -> None:
        assert parse(label) == expected

    @pytest.mark.parametrize("label", ["", "R-04", "T2A", "ABCD1"])
    def test_what_is_not_a_label(self, label: str) -> None:
        assert parse(label) is None


class TestOrdering:
    @pytest.mark.parametrize(
        ("earlier", "later"),
        [("R03", "R04"), ("A", "B"), ("Z", "AA"), ("P01", "P02"), ("T4", "C1"), ("P05", "T1")],
    )
    def test_the_default_scheme(self, earlier: str, later: str) -> None:
        assert compare(earlier, later, DEFAULT) == -1
        assert compare(later, earlier, DEFAULT) == 1

    def test_the_scheme_decides_not_the_alphabet(self) -> None:
        """Alphabetically C1 < T4. On a tender-then-construction project it is later."""
        assert compare("C1", "T4", TENDER_THEN_CONSTRUCTION) == 1

    def test_a_series_outside_the_scheme_is_ordered_by_date_or_not_at_all(self) -> None:
        assert compare("A", "B", TENDER_THEN_CONSTRUCTION) is None
        assert (
            compare(
                "A", "B", TENDER_THEN_CONSTRUCTION, a_date=date(2026, 5, 1), b_date=date(2026, 6, 1)
            )
            == -1
        )

    def test_the_same_label_is_the_same_revision(self) -> None:
        assert compare("r04", "R04", DEFAULT) == 0

    def test_latest_is_none_when_the_order_cannot_be_settled(self) -> None:
        assert latest([Candidate("1", "X1"), Candidate("2", "Y1")], DEFAULT) is None


@given(
    numbers=st.lists(st.integers(min_value=1, max_value=99), min_size=1, max_size=12, unique=True),
    prefix=st.sampled_from(["R", "P", "T", "C", ""]),
    data=st.data(),
)
def test_the_highest_revision_wins_whatever_order_it_arrives_in(
    numbers: list[int], prefix: str, data: st.DataObject
) -> None:
    labels = data.draw(st.permutations([f"{prefix}{number:02d}" for number in numbers]))

    winner = latest([Candidate(str(index), label) for index, label in enumerate(labels)], DEFAULT)

    assert winner is not None
    assert winner.label == f"{prefix}{max(numbers):02d}"


@given(
    tender=st.lists(st.integers(1, 9), min_size=1, max_size=5, unique=True),
    construction=st.lists(st.integers(1, 9), min_size=0, max_size=5, unique=True),
    data=st.data(),
)
def test_a_superseded_series_never_wins(
    tender: list[int], construction: list[int], data: st.DataObject
) -> None:
    labels = data.draw(st.permutations([f"T{n}" for n in tender] + [f"C{n}" for n in construction]))

    winner = latest(
        [Candidate(str(i), label) for i, label in enumerate(labels)], TENDER_THEN_CONSTRUCTION
    )

    assert winner is not None
    expected = f"C{max(construction)}" if construction else f"T{max(tender)}"
    assert winner.label == expected


class TestFilenames:
    NUMBER = "FP-L05-201"

    @pytest.mark.parametrize(
        ("filename", "revision"),
        [
            ("FP-L05-201-R03.pdf", "R03"),
            ("FP-L05-201_RevC.pdf", "C"),
            ("FP-L05-201 (C1).pdf", "C1"),
            ("FP-L05-201[T2].dwg", "T2"),
            ("SYNTH-001-FP-L05-201-R03.dxf", "R03"),
            ("FP_L05_201_R04.pdf", "R04"),
            ("FP-L05-201 Rev P02 - Sprinkler Layout.pdf", "P02"),
        ],
    )
    def test_a_revision_after_the_number_is_read(self, filename: str, revision: str) -> None:
        assert revision_from_filename(filename, self.NUMBER) == revision

    @pytest.mark.parametrize(
        "filename",
        ["FP-L05-201.pdf", "FP-L05-201-SIGNED.pdf", "FP-L05-2010-R01.pdf", "spec.pdf"],
    )
    def test_a_filename_that_says_nothing_is_not_read_as_saying_something(
        self, filename: str
    ) -> None:
        assert revision_from_filename(filename, self.NUMBER) is None


class TestSources:
    def test_sources_that_agree(self) -> None:
        assert reconcile("R04", "r04", None).revision == "R04"

    def test_a_silent_source_does_not_disagree(self) -> None:
        agreement = reconcile("R04", None, None)
        assert agreement.revision == "R04"
        assert agreement.conflict is None

    def test_disagreement_is_named_for_a_person(self) -> None:
        agreement = reconcile("R04", "R03", "R04")
        assert agreement.revision is None
        assert agreement.conflict is not None
        assert "the title block says R04" in agreement.conflict
        assert "the filename says R03" in agreement.conflict


class TestConfiguration:
    def test_the_shipped_schemes_load(self) -> None:
        loaded = schemes()
        assert "default" in loaded
        assert loaded["tender-then-construction"].series == ("T", "C")

    def test_an_unknown_scheme_falls_back_to_the_default(self) -> None:
        assert scheme_named("no-such-scheme").name == "default"

    def test_a_scheme_listing_a_series_twice_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "revisions.yaml"
        path.write_text(
            "schemes:\n  default:\n    effective_from: 2026-01-01\n    series: [T, T]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="twice"):
            schemes.__wrapped__(path)

    def test_a_scheme_is_a_value(self) -> None:
        assert Scheme("x", ("T",), date(2026, 1, 1)).position("T3") == (0, 3)

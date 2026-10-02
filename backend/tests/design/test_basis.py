"""The design basis: the rules' seed, and the criteria a tender's notes state (FR-DSN-01)."""

from __future__ import annotations

import pytest

from firebid.design import basis
from firebid.design.basis import NoteLine

pytestmark = pytest.mark.req("FR-DSN-01")


def column(*texts: str, x: float = 900.0, y: float = 600.0) -> list[NoteLine]:
    return [NoteLine(text, x, y + 4.0 * index, 2.5) for index, text in enumerate(texts)]


MOH = column(
    "SPRINKLER DESIGN CRITERIA FOR THE CONCEALED LAYER OF HOSPITAL CONCOURSE",
    "(ORDINARY HAZARD GROUP III-SPECIAL)",
    "MAXIMUM SPACING: (4M X 3M)",
    "MAXIMUM AREA COVERAGE PER SPRINKLER - 12M²",
    "ASSUMED MAXIMUM AREA OF OPERATION - 360M²",
    "K-FACTOR : 5.6 (80)",
    "EXPOSED LAYER OF HOSPITAL CONCOURSE VERANDAH WITH CEILING HEIGHT > 9M",
    "MAXIMUM SPACING: (3M X 3M)",
    "MAXIMUM AREA COVERAGE PER SPRINKLER - 12M²",
    "K-FACTOR : 11.2",
    "SPRINKLER TYPE : QUICK/STANDARD RESPONSE",
)


def test_each_criterion_in_the_notes_is_read_with_the_words_it_came_from() -> None:
    first, second = basis.criteria_from_notes(MOH, "FP-10-01")

    assert (first.max_spacing_mm, first.max_area_m2, first.k_factor) == (
        (4000, 3000),
        12.0,
        "5.6 (80)",
    )
    assert first.title == "(ORDINARY HAZARD GROUP III-SPECIAL)"
    assert "FP-10-01" in first.source and "MAXIMUM SPACING: (4M X 3M)" in first.source
    assert (second.max_spacing_mm, second.k_factor) == ((3000, 3000), "11.2")
    assert second.title.startswith("EXPOSED LAYER")
    assert second.response == "QUICK/STANDARD RESPONSE"
    assert first.response is None
    assert [first.key, second.key] == ["note_1", "note_2"]


def test_a_spacing_with_no_area_is_not_a_criterion() -> None:
    notes = column("DESIGN CRITERIA", "MAXIMUM SPACING: (4M X 3M)")

    assert basis.criteria_from_notes(notes, "S1") == []


def test_notes_in_another_column_do_not_complete_a_criterion() -> None:
    notes = column("DESIGN CRITERIA", "MAXIMUM SPACING: (4M X 3M)") + column(
        "MAXIMUM AREA COVERAGE PER SPRINKLER - 9M²", x=300.0
    )

    assert basis.criteria_from_notes(notes, "S1") == []


def test_the_longer_spacing_comes_first_however_it_is_written() -> None:
    notes = column(
        "DESIGN CRITERIA", "MAX. SPACING - 3.0M x 3.7M", "MAX AREA COVERAGE PER HEAD: 9M2"
    )

    (criterion,) = basis.criteria_from_notes(notes, "S1")
    assert criterion.max_spacing_mm == (3700, 3000)
    assert criterion.max_area_m2 == 9.0


def test_where_the_notes_state_none_the_rules_default_stands_and_says_so() -> None:
    assert basis.criteria_from_notes(column("GENERAL NOTES", "ALL PIPES TO BS EN 10255"), "S") == []

    default = basis.default_criterion()
    assert (default.max_area_m2, default.max_spacing_mm) == (12.0, (4000, 4000))
    assert default.source == "design rules default (to be confirmed)"


def test_the_seed_rules_load_and_every_value_is_to_be_confirmed() -> None:
    rules = basis.load_rules()

    assert (rules.version, rules.status) == (1, "to be confirmed")
    assert rules.grid_mm == (2800, 2800)
    assert [rules.range_dn(n) for n in (1, 2, 3, 4, 9, 18, 48, 49)] == [
        25, 25, 32, 40, 50, 65, 80, 100,
    ]  # fmt: skip
    assert rules.range_dn(5000) == 100
    assert basis.space_settings().door_gap_mm == 1200.0


def test_a_stored_version_of_the_rules_replaces_the_seed() -> None:
    data = basis.seed() | {"grid_mm": [3000, 2500], "status": "confirmed"}

    rules = basis.load_rules(data, version=4)
    assert (rules.version, rules.status, rules.grid_mm) == (4, "confirmed", (3000, 2500))
    assert basis.default_criterion(data).source == "design rules default (confirmed)"

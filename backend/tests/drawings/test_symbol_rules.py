"""The keyword rules that type legend rows without a model (FR-VIS-02)."""

from __future__ import annotations

import pytest

from firebid.drawings.symbol_rules import load

pytestmark = pytest.mark.req("FR-VIS-02")


@pytest.mark.parametrize(
    ("description", "object_type"),
    [
        ("PENDENT SPRINKLER", "sprinkler_pendent"),
        ("SIDEWALL SPRINKLER", "sprinkler_sidewall"),
        ("SIDE WALL SPRINKLER", "sprinkler_sidewall"),
        # Seen on a real tender set: the hyphen was not matched.
        ("SIDE-WALL SPRINKLER", "sprinkler_sidewall"),
        ("CONCEALED SPRINKLER", "sprinkler_concealed"),
        ("GATE VALVE", "gate_valve"),
        ("NON-RETURN VALVE", "check_valve"),
    ],
)
def test_a_row_the_rules_can_read_is_typed(description: str, object_type: str) -> None:
    found = load().propose(description)

    assert found is not None and found.object_type == object_type


@pytest.mark.parametrize(
    "description",
    [
        # Exposed could be pendent or upright: a person or the model says which.
        "EXPOSED SPRINKLER",
        "MANUAL CALL POINT",
    ],
)
def test_a_row_the_rules_cannot_read_is_left_for_a_person(description: str) -> None:
    assert load().propose(description) is None

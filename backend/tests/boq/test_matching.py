"""Client BOQ lines to measured lines, by rule (FR-BOQ-02)."""

from __future__ import annotations

import pytest

from firebid.boq.matching import Client, Measured, match
from firebid.evals.synthetic_boq import SECTIONS

pytestmark = pytest.mark.req("FR-BOQ-02")

# The company BOQ the synthetic installation's takeoff gives (see test_generate).
MEASURED = [
    Measured("A1", "Pendent sprinkler head, K80, chrome finish", "nr"),
    Measured("A2", "Sidewall sprinkler head, K80, white finish", "nr"),
    Measured("A3", "Upright sprinkler head, K80, chrome, white finish", "nr"),
    Measured("A4", "100 mm black steel pipe, grooved joints (main)", "m"),
    Measured("A5", "150 mm black steel pipe, grooved joints (main)", "m"),
    Measured("A6", "150 mm pipe (riser)", "m"),
    Measured("A7", "25 mm pipe (sprinkler drop)", "m"),
    Measured("A8", "50 mm black steel pipe, threaded joints (branch)", "m"),
    Measured("A9", "Grooved coupling, DN100 (rule-derived)", "nr"),
    Measured("A10", "Grooved coupling, DN150 (rule-derived)", "nr"),
    Measured("A11", "Reducer, 150 mm", "nr"),
    Measured("A12", "Tee, DN100xDN50 (rule-derived)", "nr"),
    Measured("A13", "Tee, DN150xDN50 (rule-derived)", "nr"),
    Measured("A14", "150 mm check valve", "nr"),
    Measured("A15", "150 mm gate valve", "nr"),
]
EXPECTED = {
    "A1": "A1",
    "A2": "A3",
    "A3": "A2",
    "B1": "A5",
    "B2": "A4",
    "B3": "A8",
    "B4": "A7",
    "B5": "A6",
    "C1": "A15",
    "C2": "A14",
    "C4": "A11",
    "D1": None,
    "D2": None,
}


def clients() -> list[Client]:
    return [
        Client(
            line.item,
            line.description,
            line.unit,
            "provisional" if letter == "D" else "line",
        )
        for letter, _, lines in SECTIONS
        for line in lines
    ]


def test_the_synthetic_bill_is_matched_by_rule_where_it_can_be() -> None:
    proposals, left = match(clients(), MEASURED)

    assert {p.ref: p.maps_to for p in proposals} == EXPECTED
    # The flow switch has no measured line: the rules say nothing, the model is asked.
    assert [c.ref for c in left] == ["C3"]
    assert all(p.reason for p in proposals)


def test_two_candidates_are_left_for_the_model() -> None:
    measured = [*MEASURED, Measured("A16", "150 mm stainless steel pipe (main)", "m")]

    proposals, left = match(
        [Client("B1", "150 mm diameter pipe, grooved joints", "m", "line")], measured
    )

    assert proposals == [] and [c.ref for c in left] == ["B1"]


def test_a_unit_that_disagrees_is_no_match() -> None:
    proposals, left = match([Client("X", "150 mm gate valve", "m", "line")], MEASURED)

    assert proposals == [] and left

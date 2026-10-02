"""Designed quantities in the takeoff: proposed heads and range pipe (FR-DSN-04).

A design-intent sheet's proposed layout reaches the engine as detections and runs marked
`designed`. They become rule-derived items of their own, beside what was drawn and never
added into it, each with the design rule, its version and the criterion it followed.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from firebid.qto import generate
from firebid.qto.model import DESIGNED, Detection, ItemDraft, Placement, Run
from tests.qto.conftest import CEILING, RULES, Tender, by_description, spec

pytestmark = pytest.mark.req("FR-DSN-04")

AT = Placement("sheet-d", "FP-L10-01", "00", "doc-d", "view-d", "plan", "L10", None)
RULE = {
    "rule_key": "sprinkler_layout",
    "rule_version": 3,
    "rule_status": "confirmed",
    "inputs": [
        {
            "name": "max_area_m2",
            "value": 12.0,
            "source": "sheet FP-L10-01 notes: 'MAXIMUM AREA COVERAGE PER SPRINKLER - 12M'",
        }
    ],
}


def head(index: int, *, space: int = 0, temperature: int = 68, kind: str = "object") -> Detection:
    return Detection(
        id=f"h{index}-{kind}",
        at=AT,
        kind=kind,
        object_type="sprinkler_pendent",
        category="sprinkler" if kind == "object" else "pipe",
        attributes={"temperature_rating_c": temperature},
        x=10.0 + index,
        y=20.0,
        grid_reference=None,
        grid_index=None,
        confidence=0.5,
        method=DESIGNED,
        evidence={"rule": RULE, "space": {"index": space, "name": "WARD"}},
    )


def pipe(index: int, dn: int, length_mm: int, *, remote: bool = False) -> Run:
    return Run(
        id=f"r{index}",
        at=AT,
        run_class="branch",
        dn=dn,
        size_status="labelled",
        length_mm=length_mm,
        points=((10.0, 20.0), (40.0, 20.0)),
        grid_reference=None,
        grid_points=(None, None),
        confidence=0.5,
        origin=DESIGNED,
        evidence={"rule": RULE, "remote": remote},
    )


def items(detections: list[Detection], runs: list[Run]) -> list[ItemDraft]:
    return generate.generate(detections, runs, spec, RULES, [CEILING])


def test_proposed_heads_are_an_item_of_their_own_with_the_rule_they_follow() -> None:
    (item,) = items([head(i, space=i // 2) for i in range(6)], [])

    assert item.net_quantity == Decimal(6)
    assert item.calculation_method == "rule_derived"
    assert item.detection_method == DESIGNED
    assert item.description.endswith("[proposed layout, not drawn]")
    assert item.rule is not None
    assert (item.rule["rule_key"], item.rule["rule_version"]) == ("sprinkler_layout", 3)
    assert item.rule["inputs"] == RULE["inputs"]
    assert item.note == "6 heads proposed in 3 spaces"
    assert item.level == "L10"
    assert len(item.geometry) == 6


def test_the_specification_decides_an_attribute_and_the_design_rule_fills_what_it_leaves() -> None:
    (item,) = items([head(0)], [])

    # The test specification states K80 and a chrome finish for pendents, and no rating.
    assert item.attributes["k_factor"]["source"] == "specification"
    assert item.attributes["temperature_rating_c"] == {"value": "68", "source": "design rule v3"}


def test_heads_at_another_rating_are_another_item() -> None:
    found = items([head(0), head(1), head(2, temperature=93)], [])

    assert sorted(item.net_quantity for item in found) == [Decimal(1), Decimal(2)]


def test_proposed_range_pipe_is_summed_by_size_and_says_what_is_allowance() -> None:
    found = by_description(
        items([], [pipe(0, 25, 5600), pipe(1, 25, 2400), pipe(2, 32, 6000, remote=True)])
    )

    dn25 = found["Pipe, DN25, range (black steel, threaded) [proposed layout, not drawn]"]
    dn32 = found["Pipe, DN32, range (black steel, threaded) [proposed layout, not drawn]"]
    assert (dn25.net_quantity, dn25.length_mm, dn25.unit) == (Decimal("8.000"), 8000, "m")
    assert dn25.calculation_method == "rule_derived"
    assert dn25.classification == "range"
    assert dn25.note == "2 lengths of range pipe and feed"
    assert dn32.note is not None and "6000 mm of it is an allowance" in dn32.note
    assert dn25.attributes["nominal_diameter_mm"] == {"value": "25", "source": "design rule v3"}


def test_a_proposed_head_has_a_drop_by_the_drop_rule() -> None:
    found = items([head(0), head(0, kind="drop"), head(1), head(1, kind="drop")], [])

    (drop,) = [item for item in found if item.classification == "drop"]
    assert drop.rule is not None and drop.rule["rule_key"] == "drop_length"
    assert drop.rule["count"] == 2


def test_what_was_drawn_is_counted_exactly_as_before(general: Tender) -> None:
    before = {item.key: item.inputs_hash() for item in general.items()}
    drawn_at = general.detections[0].at
    here = replace(AT, level=drawn_at.level, zone=drawn_at.zone)
    extra = [replace(head(i), at=here) for i in range(4)]
    after = generate.generate(
        [*general.detections, *extra],
        [*general.runs, replace(pipe(0, 25, 5600), at=here)],
        spec,
        RULES,
        [CEILING],
    )

    kept = {item.key: item.inputs_hash() for item in after if item.detection_method != DESIGNED}
    assert kept == before
    assert sum(1 for item in after if item.detection_method == DESIGNED) == 2


def test_a_new_version_of_the_design_rule_is_a_new_item() -> None:
    (old,) = items([head(0)], [])
    newer = replace(head(0), evidence={"rule": RULE | {"rule_version": 4}, "space": {"index": 0}})
    (new,) = items([newer], [])

    assert old.key != new.key

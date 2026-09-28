"""Fittings the drawing does not show, by rule from the pipe network (FR-QTO-04).

The synthetic installation gives the tees and couplings; small hand-drawn networks give
the elbows and reducers it has none of, and show a drawn fitting taking precedence.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import Any

import pytest

from firebid.qto import fittings
from firebid.qto.model import Detection, Placement, Run, SpecValue
from tests.qto.conftest import RULES, Tender, by_description

AT = Placement("S1", "FP-L02-201", "A", "D1", "V1", "plan", "L02", None)


def run(ident: str, dn: int, *points: tuple[float, float]) -> Run:
    # 1 mm on paper is 100 mm: lengths follow from the points at 1:100.
    length = sum(
        round(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 * 100) for a, b in pairwise(points)
    )
    return Run(ident, AT, "main", dn, "labelled", length, points, None, (None,) * len(points), 0.9)


def symbol(category: str, x: float, y: float) -> Detection:
    return Detection("d1", AT, "object", category, category, {}, x, y, None, None, 0.9, "shape")


def derived(runs: list[Run], drawn: list[Detection] | None = None) -> dict[str, Any]:
    items = fittings.derive(
        runs, drawn or [], RULES, lambda dn: [SpecValue("threaded", "2.1", {})], None, {}
    )
    return {item.description: item for item in items}


@pytest.mark.req("FR-QTO-04")
class TestFromTheSyntheticInstallation:
    def test_a_tee_at_every_branch_connection(self, general: Tender) -> None:
        items = by_description(general.items())

        assert items["Tee, DN150xDN50 (rule-derived: not drawn)"].net_quantity == 3
        assert items["Tee, DN100xDN50 (rule-derived: not drawn)"].net_quantity == 3

    def test_grooved_couplings_per_joint_and_connected_end(self, general: Tender) -> None:
        items = by_description(general.items())

        # Every DN150 run is shorter than a 6 m random length, and meets a symbol or tee at
        # both ends: 6 runs x 2. DN100: three runs with two, the open end of the main one.
        dn150 = items["Grooved coupling, DN150 (rule-derived: not drawn)"]
        dn100 = items["Grooved coupling, DN100 (rule-derived: not drawn)"]
        assert (dn150.net_quantity, dn100.net_quantity) == (Decimal(12), Decimal(7))
        assert dn150.rule is not None and dn150.rule["rule_key"] == "grooved_couplings"
        assert all("random_length_mm" in str(i) for i in dn150.rule["inputs"])

    def test_threaded_pipe_gets_no_couplings(self, general: Tender) -> None:
        items = general.items()

        assert not [i for i in items if i.description.startswith("Grooved coupling, DN50")]

    def test_every_derived_fitting_is_labelled_with_its_rule(self, general: Tender) -> None:
        derived_items = [i for i in general.items() if i.item_type.startswith("fitting_")]

        assert derived_items
        for item in derived_items:
            assert item.calculation_method == "rule_derived"
            assert item.rule is not None
            assert item.rule["rule_version"] == 1 and item.rule["inputs"]


@pytest.mark.req("FR-QTO-04")
class TestHandDrawnNetworks:
    def test_an_elbow_at_a_turn_within_a_run(self) -> None:
        items = derived([run("r1", 80, (0, 0), (30, 0), (30, 20))])

        assert items["Elbow, DN80 (rule-derived: not drawn)"].net_quantity == 1

    def test_an_elbow_where_two_runs_meet_at_a_corner_but_not_straight_on(self) -> None:
        corner = derived([run("r1", 80, (0, 0), (30, 0)), run("r2", 80, (30, 0), (30, 20))])
        straight = derived([run("r1", 80, (0, 0), (30, 0)), run("r2", 80, (30, 0), (60, 0))])

        assert corner["Elbow, DN80 (rule-derived: not drawn)"].net_quantity == 1
        assert straight == {}

    def test_a_reducer_where_the_size_changes(self) -> None:
        items = derived([run("r1", 80, (0, 0), (30, 0)), run("r2", 50, (30, 0), (60, 0))])

        assert items["Reducer, DN80xDN50 (rule-derived: not drawn)"].net_quantity == 1

    def test_a_drawn_fitting_takes_precedence(self) -> None:
        runs = [run("r1", 80, (0, 0), (30, 0)), run("r2", 50, (30, 0), (60, 0))]

        assert derived(runs, [symbol("fitting", 30, 0)]) == {}

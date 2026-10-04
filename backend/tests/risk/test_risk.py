"""Bid risk without a database: the checklist's proposals, design-responsibility and
execution findings with their evidence, and the impact arithmetic."""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.evals.synthetic_spec import (
    EXPECTED_DESIGN_RISKS,
    EXPECTED_WORDING_RISKS,
    with_risks,
)
from firebid.risk import impact, rules
from firebid.risk.rules import ScopeFact, Words

CLAUSES = [(number, heading, text) for number, heading, text in with_risks()]


def fact(kind: str, key: str, status: str, clause: str | None = "7.1") -> ScopeFact:
    return ScopeFact("sprinkler", kind, key, status, clause, "the clause's words")


@pytest.mark.req("FR-RSK-01")
class TestChecklist:
    def test_the_shipped_checklist_covers_every_item_the_requirement_names(self) -> None:
        keys = [item["key"] for item in rules.settings()["checklist"]]

        assert keys == [
            "pumps",
            "tanks",
            "breeching_inlets",
            "hydrants",
            "hose_reels",
            "hydraulic_calculations",
            "shop_drawings",
            "testing_commissioning",
            "authority_fsc",
            "builders_works",
            "power_supply",
        ]

    def test_statuses_are_proposed_from_the_scope_matrix_and_the_takeoff(self) -> None:
        scope = [
            fact("interface", "power_supply", "by_others"),
            fact("interface", "builders_works", "excluded", "7.3"),
            fact("obligation", "submittals", "included", "6.6"),
            fact("obligation", "testing", "included", "6.1"),
            fact("obligation", "commissioning", "unclear", None),
        ]

        found = {
            check.key: check
            for check in rules.checklist(["sprinkler"], scope, {"fire_pump": Decimal(2)})
        }

        assert (found["power_supply"].proposed, found["builders_works"].proposed) == (
            "by_others",
            "excluded",
        )
        assert found["power_supply"].basis == "the scope matrix has it by others"
        assert found["power_supply"].evidence[0]["label"] == "Specification clause 7.1"
        assert (
            found["shop_drawings"].proposed
            == found["hydraulic_calculations"].proposed
            == ("included")
        )
        assert found["pumps"].proposed == "included"
        assert found["pumps"].basis == "the takeoff counts 2 fire pump"
        # Rows that do not agree, and what nothing settles, are left for a person.
        assert found["testing_commissioning"].proposed == "open"
        assert found["testing_commissioning"].basis == "the scope matrix's rows do not agree on it"
        assert found["tanks"].proposed == found["authority_fsc"].proposed == "open"
        assert found["tanks"].basis == "neither the scope matrix nor the takeoff settles it"
        # An item limited to other systems is not asked of this one.
        assert "hydrants" not in found and "hose_reels" not in found

    def test_each_system_has_its_own_items(self) -> None:
        found = rules.checklist(["hydrant", "sprinkler"], [], {})

        assert [c.key for c in found if c.system == "hydrant"][:3] == ["pumps", "tanks", "hydrants"]
        assert "breeching_inlets" in [c.key for c in found if c.system == "sprinkler"]
        assert all(check.proposed in rules.STATUSES for check in found)


@pytest.mark.req("FR-RSK-02")
class TestDesignResponsibility:
    def test_design_clauses_yield_cited_risks(self) -> None:
        found = {risk.kind: risk for risk in rules.design_risks(CLAUSES)}

        assert {
            kind: tuple(item["clause"] for item in risk.evidence) for kind, risk in found.items()
        } == dict(EXPECTED_DESIGN_RISKS)
        build = found["design_and_build"]
        assert build.category == "design_responsibility"
        assert build.evidence[0]["quote"] == (
            "The sprinkler installation shall be procured on a design and build basis."
        )
        assert (build.treatment, found["hydraulic_calculations"].treatment) == ("qualify", "price")
        assert build.title == "Design and build: the contractor's responsibility"
        qp = found["qp_engagement"]
        assert "shall engage a Qualified Person" in qp.evidence[0]["quote"]

    def test_a_specification_without_such_clauses_yields_none(self) -> None:
        plain = [("2.1", "Pipework", "Pipes shall be black steel to BS EN 10255.")]

        assert rules.design_risks(plain) == []

    def test_assisting_the_qp_is_not_engaging_one(self) -> None:
        clause = [("6.7", "", "The Contractor shall assist the Qualified Person in obtaining it.")]

        assert rules.design_risks(clause) == []


@pytest.mark.req("FR-RSK-03")
class TestExecution:
    def found(self) -> dict[str, rules.Finding]:
        words = [
            *(Words("clause", f"Specification clause {n}", text) for n, _, text in CLAUSES if text),
            Words(
                "sheet", "Drawing FP-B1-201 rev R01", "CEILING VOID CONGESTED WITH ACMV DUCTS", "B1"
            ),
        ]
        risks = rules.execution_risks(
            ["B1", "B2", "L05"],
            {"B1": (Decimal(5200), "entered by Esther"), "L05": (Decimal(2750), "note")},
            (Decimal(24), "entered by Esther"),
            words,
        )
        return {risk.key: risk for risk in risks}

    def test_seeded_conditions_are_flagged_with_their_evidence(self) -> None:
        found = self.found()

        basement = found["execution:basement"]
        assert basement.title == "Basement levels: B1, B2"
        assert [e["label"] for e in basement.evidence] == [
            "Level B1 on the drawings",
            "Level B2 on the drawings",
        ]
        height = found["execution:work_at_height:B1"]
        assert (height.level, height.multiplier) == ("B1", "height")
        assert height.evidence[0]["quote"] == "5200 mm (entered by Esther)"
        assert "execution:work_at_height:L05" not in found, "2750 mm is under the threshold"
        assert found["execution:high_rise"].evidence[0]["quote"] == "24 (entered by Esther)"
        for kind, clause in EXPECTED_WORDING_RISKS:
            risk = found[f"execution:{kind}"]
            assert [e["label"] for e in risk.evidence] == [f"Specification clause {clause}"]
        night = found["execution:night_work"]
        assert "at night between 2200 and 0600" in night.evidence[0]["quote"]
        assert night.multiplier == "night_work" and found["execution:shutdown"].multiplier is None
        # A drawing note is evidence too.
        congested = found["execution:congested_ceilings"]
        assert congested.evidence[0] == {
            "kind": "sheet",
            "label": "Drawing FP-B1-201 rev R01",
            "quote": "CEILING VOID CONGESTED WITH ACMV DUCTS",
        }
        assert all(risk.evidence and risk.category == "execution" for risk in found.values())

    def test_nothing_is_flagged_without_something_to_show_for_it(self) -> None:
        assert rules.execution_risks(["L01", "L02"], {}, (Decimal(5), "entered"), []) == []


@pytest.mark.req("FR-RSK-04")
class TestImpact:
    def test_a_multiplier_s_extra_hours_and_cost_on_the_labour_it_reaches(self) -> None:
        reach = impact.LabourReach(lines=3, hours=Decimal("120.00"), cost=Decimal("2400.00"))

        found = impact.labour_impact(
            reach,
            "night_work",
            "Night work",
            Decimal("1.20"),
            already_applied=False,
            scope="the whole bid",
        )

        # 120 h x (1.20 - 1) = 24 h more; SGD 2,400 x 0.20 = 480.
        assert (found.method, found.hours, found.cost) == (
            "labour_multiplier",
            Decimal("24.00"),
            Decimal("480.00"),
        )
        assert found.basis == (
            '"Night work" (x 1.20) on 120.00 man-hours over 3 bill line(s) on the whole bid: '
            "24.00 man-hours more, SGD 480.00 at the trades' rates"
        )

    def test_a_multiplier_already_in_the_estimate_adds_nothing_more(self) -> None:
        reach = impact.LabourReach(lines=3, hours=Decimal("120.00"), cost=Decimal("2400.00"))

        found = impact.labour_impact(
            reach, "night_work", "Night work", Decimal("1.20"), already_applied=True, scope="B1"
        )

        assert (found.method, found.cost, found.hours) == (
            "already_priced",
            Decimal("0.00"),
            Decimal("0.00"),
        )
        assert "24.00 man-hours, SGD 480.00, are already in the labour cost" in found.basis

    def test_with_no_labour_to_reach_nothing_is_computed(self) -> None:
        found = impact.labour_impact(
            impact.LabourReach(0, Decimal(0), Decimal(0)),
            "basement",
            "Basement",
            Decimal("1.10"),
            already_applied=False,
            scope="B1",
        )

        assert (found.method, found.cost) == ("not_computed", None)
        assert "no labour is estimated on B1 yet" in found.basis

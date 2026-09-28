"""The company BOQ from the synthetic installation's takeoff (FR-BOQ-01, FR-ADM-03), and the
reconciliation arithmetic and conventions (FR-BOQ-03, FR-BOQ-06)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.boq import generate, reconcile
from firebid.boq.generate import Item, Template
from firebid.evals import synthetic_qto as fixture
from firebid.evals.qto_pipeline import Sheet, read
from firebid.qto import dedup
from firebid.qto import generate as takeoff
from tests.qto.conftest import CEILING, RULES, spec

TEMPLATE = generate.seed_templates()[0]


@pytest.fixture(scope="module")
def items() -> list[Item]:
    document, _ = fixture.general_arrangement()
    detections, runs = read([Sheet("FP-L05-201", document)])
    groups = dedup.find(detections, runs)
    excluded, lengths = dedup.exclusions(groups)
    drafts = takeoff.generate(
        detections, runs, spec, RULES, [CEILING], excluded=excluded, excluded_length=lengths
    )
    return [
        Item(
            id=f"i{index}",
            human_id=f"QTO-{index:06d}",
            item_type=d.item_type,
            classification=d.classification,
            description=d.description,
            attributes={k: str(v.get("value")) for k, v in d.attributes.items()},
            unit=d.unit,
            net_quantity=d.net_quantity,
            allowance_percent=d.allowance_percent,
            level=d.level,
        )
        for index, d in enumerate(drafts, start=1)
    ]


@pytest.mark.req("FR-BOQ-01")
class TestGeneration:
    def test_lines_descriptions_and_quantities(self, items: list[Item]) -> None:
        lines = generate.generate(items, TEMPLATE)

        by_description = {(ln.group, ln.description): ln for ln in lines}
        assert [(ln.item_no, ln.description, ln.unit, str(ln.quantity)) for ln in lines] == [
            ("A1", "Pendent sprinkler head, K80, chrome finish", "nr", "16.000"),
            ("A2", "Sidewall sprinkler head, K80, white finish", "nr", "4.000"),
            ("A3", "Upright sprinkler head, K80, chrome, white finish", "nr", "4.000"),
            ("A4", "100 mm black steel pipe, grooved joints (main)", "m", "8.250"),
            ("A5", "150 mm black steel pipe, grooved joints (main)", "m", "8.050"),
            ("A6", "150 mm pipe (riser)", "m", "4.000"),
            ("A7", "25 mm pipe (sprinkler drop)", "m", "12.000"),
            ("A8", "50 mm black steel pipe, threaded joints (branch)", "m", "72.000"),
            ("A9", "Grooved coupling, DN100 (rule-derived)", "nr", "7.000"),
            ("A10", "Grooved coupling, DN150 (rule-derived)", "nr", "12.000"),
            ("A11", "Reducer, 150 mm", "nr", "1.000"),
            ("A12", "Tee, DN100xDN50 (rule-derived)", "nr", "3.000"),
            ("A13", "Tee, DN150xDN50 (rule-derived)", "nr", "3.000"),
            ("A14", "150 mm check valve", "nr", "1.000"),
            ("A15", "150 mm gate valve", "nr", "1.000"),
        ]
        assert {ln.section for ln in lines} == {"FIRE SPRINKLER INSTALLATION"}
        heads = by_description[("Sprinkler heads", "Pendent sprinkler head, K80, chrome finish")]
        assert heads.level == "L05"  # heads roll up by level
        assert by_description[
            ("Pipework", "50 mm black steel pipe, threaded joints (branch)")
        ].allowance_percent == Decimal(5)

    def test_every_line_keeps_the_items_it_came_from(self, items: list[Item]) -> None:
        lines = generate.generate(items, TEMPLATE)

        assert sorted(i for ln in lines for i in ln.item_ids) == sorted(i.id for i in items)
        assert all(ln.item_ids for ln in lines)

    def test_the_same_items_give_the_same_lines(self, items: list[Item]) -> None:
        first = generate.generate(items, TEMPLATE)
        second = generate.generate(list(reversed(items)), TEMPLATE)

        assert [(ln.item_no, ln.description, ln.quantity) for ln in first] == [
            (ln.item_no, ln.description, ln.quantity) for ln in second
        ]


@pytest.mark.req("FR-ADM-03")
def test_a_template_edit_changes_the_wording_and_the_roll_up(items: list[Item]) -> None:
    definition = dict(TEMPLATE.definition)
    definition["descriptions"] = {
        **definition["descriptions"],
        "pipe": ["{nominal_diameter_mm}mm dia. ", "{pipe_material} pipe"],
    }
    definition["groups"] = [
        {**g, "by": "level"} if g["key"] == "pipework" else g for g in definition["groups"]
    ]
    edited = Template(TEMPLATE.key, 2, TEMPLATE.title, definition, "confirmed")

    lines = generate.generate(items, edited)

    branch = next(ln for ln in lines if ln.description.startswith("50mm dia."))
    assert branch.description == "50mm dia. black steel pipe"
    assert branch.level == "L05"


@pytest.mark.req("FR-BOQ-03")
class TestVariance:
    def test_the_appendix_b_example(self) -> None:
        found = reconcile.variance(Decimal(120), Decimal("128.4"), Decimal(5))

        assert (found.difference, found.percent, found.flagged) == (
            Decimal("8.400"),
            Decimal("7.0"),
            True,
        )

    def test_within_the_threshold_is_not_flagged_either_way(self) -> None:
        assert not reconcile.variance(Decimal(70), Decimal(72), Decimal(5)).flagged  # +2.9%
        assert reconcile.variance(Decimal(100), Decimal(94), Decimal(5)).flagged  # -6.0%

    def test_a_client_line_with_no_quantity_is_flagged_when_something_is_measured(self) -> None:
        assert reconcile.variance(None, Decimal(3), Decimal(5)) == reconcile.Variance(
            Decimal(3), None, True
        )


@pytest.mark.req("FR-BOQ-06")
class TestConventions:
    def test_the_defaults_word_the_qualification(self) -> None:
        text = reconcile.qualification_text({})

        assert text.startswith("Measurement conventions\n1. Pipework is measured net")
        assert "Fittings are enumerated separately" in text

    def test_a_setting_changes_its_clause_and_a_wrong_one_is_refused(self) -> None:
        text = reconcile.qualification_text({"fittings": "deemed_included"})

        assert "Fittings are deemed included in the rates" in text
        with pytest.raises(ValueError, match="not one of"):
            reconcile.qualification_text({"fittings": "sometimes"})
        with pytest.raises(ValueError, match="unknown convention"):
            reconcile.qualification_text({"colour": "red"})

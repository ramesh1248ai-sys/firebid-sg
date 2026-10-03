"""Hangers and seismic restraint, by rule from measured pipe (FR-QTO-07).

Checked against hand calculations on the synthetic installation, whose pipe is known to the
millimetre: 72,000 mm of DN50 branch, 8,250 mm of DN100 and 8,050 mm of DN150 main.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from firebid.qto import hangers, rules
from firebid.qto.model import ItemDraft, SpecValue
from tests.qto.conftest import RULES, Tender, citation
from tests.qto.conftest import spec as plain

pytestmark = pytest.mark.req("FR-QTO-07")

HANGER = "Pipe hanger, DN{} (rule-derived: not drawn)"
BRACE = "Seismic brace, {}, DN{} (rule-derived: not drawn)"
QUOTE = "Hangers for pipes up to and including DN 50 shall be spaced at not more than 3.0 m"


def specified(seismic: bool = False):  # type: ignore[no-untyped-def]
    """The specification's supports clauses, verified: 3.0 m to DN50, 4.0 m from DN65; and,
    when the project calls for it, seismic bracing from DN65."""

    def spec(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
        found = dict(plain(system, dn))
        if dn is None or system != "sprinkler":
            return found
        spacing = "3000" if dn <= 50 else "4000"
        found["hanger_spacing_mm"] = [
            SpecValue(spacing, "2.3.1", {"clause": "2.3.1", "quote": QUOTE})
        ]
        if seismic and dn >= 65:
            found["seismic_restraint"] = [SpecValue("required", "2.3.2", citation("2.3.2"))]
        return found

    return spec


def supports(items: list[ItemDraft]) -> dict[str, ItemDraft]:
    return {item.description: item for item in items if item.classification == "support"}


class TestHangers:
    def test_quantities_match_the_hand_calculation_from_the_specification_s_spacing(
        self, general: Tender
    ) -> None:
        found = supports(general.items(spec=specified()))

        # Length over spacing, rounded up: 72,000 / 3,000; 8,250 / 4,000; 8,050 / 4,000.
        assert {name: item.net_quantity for name, item in found.items()} == {
            HANGER.format(50): Decimal(24),
            HANGER.format(100): Decimal(3),
            HANGER.format(150): Decimal(3),
        }

    def test_each_hanger_item_cites_the_spacing_clause(self, general: Tender) -> None:
        item = supports(general.items(spec=specified()))[HANGER.format(50)]

        spacing = item.attributes["spacing_mm"]
        assert (spacing["value"], spacing["source"]) == ("3000", "specification")
        assert spacing["citations"] == [{"clause": "2.3.1", "quote": QUOTE}]
        assert item.rule is not None and item.rule["rule_key"] == "hanger_spacing"
        assert item.rule["inputs"] == [
            {"name": "length_mm", "value": 72_000, "source": "measured runs"},
            {"name": "spacing_mm", "value": 3_000, "source": "specification clause 2.3.1"},
        ]
        assert item.calculation_method == "rule_derived" and item.unit == "no"
        assert sum(m["length_mm"] for m in item.members) == 72_000

    def test_a_silent_specification_gives_the_company_default_and_says_so(
        self, general: Tender
    ) -> None:
        found = supports(general.items())

        # The seed's defaults: 3,000 to DN50, 4,000 to DN100, 4,500 above.
        assert {name: item.net_quantity for name, item in found.items()} == {
            HANGER.format(50): Decimal(24),
            HANGER.format(100): Decimal(3),
            HANGER.format(150): Decimal(2),
        }
        for item in found.values():
            source = item.attributes["spacing_mm"]["source"]
            assert source == "company default (rule hanger_spacing v1, to be confirmed)"
            assert "citations" not in item.attributes["spacing_mm"]
            assert item.rule is not None and item.rule["inputs"][1]["source"] == source

    def test_an_edited_default_is_a_new_rule_version_on_the_item(self, general: Tender) -> None:
        edited = replace(
            RULES["hanger_spacing"],
            version=2,
            status="confirmed",
            definition={"default_spacing_mm": [{"up_to_dn": 999, "spacing_mm": 2000}]},
        )

        found = supports(general.items(rule_set={**RULES, "hanger_spacing": edited}))

        assert found[HANGER.format(50)].net_quantity == Decimal(36)
        assert found[HANGER.format(50)].rule["rule_version"] == 2  # type: ignore[index]

    def test_pipe_counted_from_another_sheet_is_not_hung_twice(self, match_lined: Tender) -> None:
        found = supports(match_lined.items(spec=specified()))

        # The branch at 9 m is on both sheets of the pair: the installation is hung once.
        assert found[HANGER.format(50)].net_quantity == Decimal(24)

    def test_two_spacings_for_one_size_use_the_closer_and_record_the_conflict(self) -> None:
        values = [
            SpecValue("3000", "2.3.1", citation("2.3.1")),
            SpecValue("2500", "7.4", citation("7.4")),
        ]

        spacing, attribute = hangers.spacing_for(RULES["hanger_spacing"], 50, values)

        assert spacing == 2_500 and "conflict" in attribute

    def test_no_hanger_rule_no_hangers(self, general: Tender) -> None:
        without = {k: v for k, v in RULES.items() if k != "hanger_spacing"}

        assert supports(general.items(rule_set=without)) == {}

    def test_a_buried_hydrant_main_is_not_hung(self, systems: Tender) -> None:
        found = supports(systems.items())

        assert found and all(item.level in ("B1", "L03") for item in found.values())
        assert not any(
            "system" in item.attributes and item.level is None for item in found.values()
        )

    @pytest.mark.parametrize(
        ("length", "spacing", "expected"),
        [(0, 3000, 0), (1, 3000, 1), (3000, 3000, 1), (3001, 3000, 2), (72000, 3000, 24)],
    )
    def test_one_per_spacing_or_part_of_one(self, length: int, spacing: int, expected: int) -> None:
        assert hangers.count(length, spacing) == expected


class TestSeismicRestraint:
    def test_nothing_is_taken_off_unless_the_specification_requires_it(
        self, general: Tender
    ) -> None:
        for spec in (plain, specified(seismic=False)):
            found = supports(general.items(spec=spec))

            assert not any("Seismic" in name for name in found)

    def test_braces_appear_only_for_the_specification_that_requires_them(
        self, general: Tender
    ) -> None:
        found = supports(general.items(spec=specified(seismic=True)))

        # 8,250 mm and 8,050 mm: one lateral per 12 m and one longitudinal per 24 m, each
        # rounded up. The DN50 branches are below the clause's DN65 and are not braced.
        assert {name: item.net_quantity for name, item in found.items() if "Seismic" in name} == {
            BRACE.format("lateral", 100): Decimal(1),
            BRACE.format("longitudinal", 100): Decimal(1),
            BRACE.format("lateral", 150): Decimal(1),
            BRACE.format("longitudinal", 150): Decimal(1),
        }

    def test_a_brace_item_cites_the_clause_that_requires_it(self, general: Tender) -> None:
        item = supports(general.items(spec=specified(seismic=True)))[BRACE.format("lateral", 100)]

        required = item.attributes["seismic_restraint"]
        assert (required["value"], required["source"]) == ("required", "specification")
        assert required["citations"] == [citation("2.3.2")]
        assert item.rule is not None and item.rule["rule_key"] == "seismic_restraint"
        assert {"name": "required_by", "value": "2.3.2", "source": "specification"} in item.rule[
            "inputs"
        ]

    def test_a_specification_that_says_not_required_gives_none(self, general: Tender) -> None:
        def spec(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
            return {"seismic_restraint": [SpecValue("not_required", "2.3.2", citation("2.3.2"))]}

        assert not any("Seismic" in name for name in supports(general.items(spec=spec)))


def test_the_seed_carries_both_rules_to_be_confirmed() -> None:
    seeded = {rule.key: rule for rule in rules.seed_rules()}

    assert seeded["hanger_spacing"].status == "to be confirmed"
    assert seeded["seismic_restraint"].status == "to be confirmed"
    assert hangers.default_spacing(seeded["hanger_spacing"], 25) == 3_000
    assert hangers.default_spacing(seeded["hanger_spacing"], 65) == 4_000
    assert hangers.default_spacing(seeded["hanger_spacing"], 200) == 4_500
    assert hangers.default_spacing(seeded["hanger_spacing"], None) == 3_000

"""QTO items from the synthetic installation: counts, lengths, drops and risers (FR-QTO-01 to 05).

The installation (see `synthetic_network`) is one floor: a riser, a DN150 main through a
gate valve and a non-return valve reducing to DN100, and six DN50 branches of four heads
each (four branches of pendents, one of uprights, one of sidewalls). Its counts and lengths
are known exactly, so every quantity below is checked against a hand calculation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.qto import rules
from tests.qto.conftest import RULES, Tender, by_description


@pytest.mark.req("FR-QTO-01")
class TestCounts:
    def test_sprinklers_by_type_and_attributes(self, general: Tender) -> None:
        items = by_description(general.items())

        pendent = items["Sprinkler, pendent (k factor 80, finish chrome)"]
        sidewall = items["Sprinkler, sidewall (k factor 80, finish white)"]
        upright = items["Sprinkler, upright (k factor 80, finish chrome, white)"]

        assert (pendent.net_quantity, sidewall.net_quantity, upright.net_quantity) == (
            Decimal(16),
            Decimal(4),
            Decimal(4),
        )
        assert {pendent.unit, sidewall.unit, upright.unit} == {"no"}
        assert pendent.calculation_method == "count"
        assert len(pendent.members) == 16

    @pytest.mark.req("FR-QTO-05")
    def test_valves_and_drawn_fittings_by_type_with_their_size(self, general: Tender) -> None:
        items = by_description(general.items())

        assert items["Gate valve, DN150"].net_quantity == 1
        assert items["Check valve, DN150"].net_quantity == 1
        reducer = items["Fitting, DN150xDN100 (fitting reducer)"]
        assert reducer.net_quantity == 1
        # Both of its sizes, from the pipe drawn either side of it.
        assert reducer.attributes["nominal_diameter_mm"] == {"value": "150", "source": "drawing"}
        assert reducer.attributes["outlet_diameter_mm"] == {"value": "100", "source": "drawing"}
        assert "outlet_diameter_mm" not in items["Gate valve, DN150"].attributes

    def test_each_attribute_records_its_source(self, general: Tender) -> None:
        items = by_description(general.items())
        pendent = items["Sprinkler, pendent (k factor 80, finish chrome)"].attributes

        # The specification clause for pendents supplies the finish, and is cited.
        assert pendent["finish"]["source"] == "specification"
        assert [c["clause"] for c in pendent["finish"]["citations"]] == ["2.2.1"]
        # Nothing supplies a temperature rating: it says so rather than guessing.
        assert pendent["temperature_rating_c"] == {
            "value": "not specified",
            "source": "not specified",
        }
        valve = items["Gate valve, DN150"].attributes
        assert valve["nominal_diameter_mm"] == {"value": "150", "source": "drawing"}

    def test_a_value_no_clause_settles_is_flagged(self, general: Tender) -> None:
        items = by_description(general.items())
        upright = items["Sprinkler, upright (k factor 80, finish chrome, white)"]

        assert upright.attributes["finish"]["conflict"]
        assert "conflict" not in upright.attributes["k_factor"]

    def test_grouped_by_level_with_the_grid_range_spanned(self, general: Tender) -> None:
        items = by_description(general.items())
        pendent = items["Sprinkler, pendent (k factor 80, finish chrome)"]

        assert pendent.level == "L05"
        assert pendent.grid_from and pendent.grid_to and pendent.grid_from != pendent.grid_to
        assert [s["sheet_number"] for s in pendent.sources] == ["FP-L05-201"]
        assert pendent.sources[0]["revision"] == "R01"


@pytest.mark.req("FR-QTO-02")
class TestLengths:
    def test_net_length_per_dn_matches_the_drawing_exactly(self, general: Tender) -> None:
        items = by_description(general.items())

        # DN150: 350 + 200 + 250 + 3,000 + 3,000 + 1,250 between the riser, valves, tees
        # and the reducer. DN100: 1,250 + 3,000 + 3,000 + 1,000. DN50: six 12 m branches.
        main150 = items["Pipe, DN150, main (black steel, grooved)"]
        main100 = items["Pipe, DN100, main (black steel, grooved)"]
        branch = items["Pipe, DN50, branch (black steel, threaded)"]
        assert (main150.length_mm, main100.length_mm, branch.length_mm) == (8_050, 8_250, 72_000)
        assert main150.net_quantity == Decimal("8.050")
        assert main150.unit == "m" and main150.calculation_method == "centreline_length"

    def test_pipe_attributes_come_from_the_specification_by_size(self, general: Tender) -> None:
        items = by_description(general.items())
        branch = items["Pipe, DN50, branch (black steel, threaded)"].attributes

        assert branch["nominal_diameter_mm"]["source"] == "drawing"
        assert branch["joining_method"]["value"] == "threaded"
        assert branch["joining_method"]["source"] == "specification"
        assert branch["pipe_class"]["value"] == "not specified"


@pytest.mark.req("FR-QTO-03")
@pytest.mark.req("FR-QTO-04")
class TestRuleDerived:
    def test_drops_are_per_head_by_rule_with_every_input_and_its_source(
        self, general: Tender
    ) -> None:
        items = by_description(general.items())
        drops = items["Sprinkler drop, DN25 (vertical, not drawn)"]

        # 3,300 branch elevation - 2,750 ceiling (the sheet's note) - 50 setting = 500 each.
        assert drops.length_mm == 24 * 500
        assert drops.calculation_method == "rule_derived"
        assert drops.rule is not None
        assert (drops.rule["rule_key"], drops.rule["rule_version"]) == ("drop_length", 1)
        inputs = {i["name"]: i for i in drops.rule["inputs"]}
        assert inputs["ceiling_height_mm"] == {
            "name": "ceiling_height_mm",
            "value": 2750,
            "source": "sheet FP-L05-201 note 'CEILING HEIGHT 2750'",
        }
        assert inputs["branch_elevation_mm"]["source"] == "rule default (to be confirmed)"

    def test_a_level_parameter_overrides_the_bid_wide_one(self, general: Tender) -> None:
        parameters = [
            rules.Parameter("ceiling_height_mm", 3000, "entered by Esther Tan"),
            rules.Parameter("ceiling_height_mm", 2900, "entered by Esther Tan", "L05"),
        ]
        items = by_description(general.items(parameters))

        drops = items["Sprinkler drop, DN25 (vertical, not drawn)"]
        assert drops.length_mm == 24 * (3300 - 2900 - 50)

    def test_riser_is_floor_to_floor_times_levels_served(self, general: Tender) -> None:
        parameters = [
            rules.Parameter("floor_to_floor_mm", 4200, "section A-A", "L05"),
            rules.Parameter("levels_served", 3, "riser schematic FP-SCH-001", "L05"),
        ]
        items = by_description(general.items(parameters))

        riser = items["Riser, DN150 (vertical, not drawn)"]
        assert riser.length_mm == 4200 * 3
        assert riser.rule is not None
        assert {i["source"] for i in riser.rule["inputs"]} == {
            "section A-A",
            "riser schematic FP-SCH-001",
        }

    def test_a_riser_with_no_parameters_uses_the_rule_defaults_and_says_so(
        self, general: Tender
    ) -> None:
        items = by_description(general.items([]))

        riser = items["Riser, DN150 (vertical, not drawn)"]
        assert riser.length_mm == 4000
        assert riser.rule is not None and riser.rule["rule_status"] == "to be confirmed"


@pytest.mark.req("FR-ADM-02")
class TestRuleVersions:
    def test_items_record_the_version_of_the_rule_they_used(self, general: Tender) -> None:
        edited = rules.Rule(
            "drop_length",
            2,
            "Sprinkler drop length",
            {
                **RULES["drop_length"].definition,
                "defaults": {
                    "branch_elevation_mm": 3400,
                    "ceiling_height_mm": 2800,
                    "sprinkler_setting_mm": 50,
                },
            },
            "confirmed",
        )
        items = by_description(general.items(rule_set={**RULES, "drop_length": edited}))

        drops = items["Sprinkler drop, DN25 (vertical, not drawn)"]
        assert drops.rule is not None and drops.rule["rule_version"] == 2
        assert drops.length_mm == 24 * (3400 - 2750 - 50)


@pytest.mark.req("FR-QTO-10")
class TestNetAndAllowance:
    def test_the_allowance_is_kept_beside_the_net_never_in_it(self, general: Tender) -> None:
        items = by_description(general.items())
        branch = items["Pipe, DN50, branch (black steel, threaded)"]
        pendent = items["Sprinkler, pendent (k factor 80, finish chrome)"]

        assert branch.net_quantity == Decimal("72.000")
        assert branch.allowance_percent == Decimal("5")
        assert rules.adjusted(branch.net_quantity, branch.allowance_percent) == Decimal("75.600")
        assert pendent.allowance_percent == Decimal("0")


class TestDeterminism:
    def test_the_same_inputs_give_identical_items(self, general: Tender) -> None:
        first = [(item.key, item.inputs_hash()) for item in general.items()]
        second = [(item.key, item.inputs_hash()) for item in general.items()]

        assert first == second
        assert len({key for key, _ in first}) == len(first)

    def test_a_changed_input_changes_only_the_items_it_feeds(self, general: Tender) -> None:
        before = {item.key: item.inputs_hash() for item in general.items()}
        taller = [rules.Parameter("ceiling_height_mm", 2600, "entered by Esther Tan", "L05")]
        after = {item.key: item.inputs_hash() for item in general.items(taller)}

        changed = {key for key in before if before[key] != after[key]}
        assert before.keys() == after.keys()
        assert len(changed) == 1  # the drops, and nothing else


@pytest.mark.req("FR-QTO-04")
class TestEveryItemHasItsOwnKey:
    """An item is matched to its earlier self by key at every recompute. Two items with one
    key take each other's place: one is created anew each time and never superseded."""

    def test_no_two_items_share_a_key(self, with_enlarged_and_schematic: Tender) -> None:
        keys = [item.key for item in with_enlarged_and_schematic.items()]

        assert len(keys) == len(set(keys))

    def test_risers_on_a_sheet_with_no_grid_are_separate_items_with_stable_keys(
        self, general: Tender
    ) -> None:
        from dataclasses import replace

        from firebid.qto import generate
        from tests.qto.conftest import CEILING, spec

        riser = next(d for d in general.detections if d.kind == "riser")
        before = next(i.key for i in general.items() if i.classification == "riser")
        # Three risers the sheet cannot place on a grid, as on a plan drawn without one.
        ungridded = [
            replace(riser, id=f"riser-{n}", grid_reference=None, x=100.0 + 50 * n, y=40.0)
            for n in range(3)
        ]
        others = [d for d in general.detections if d.kind != "riser"]

        first = generate.generate([*others, *ungridded], general.runs, spec, RULES, [CEILING])
        again = generate.generate(
            [*others, *reversed(ungridded)], general.runs, spec, RULES, [CEILING]
        )

        risers = [i.key for i in first if i.classification == "riser"]
        assert len(risers) == 3 and len(set(risers)) == 3
        # The same risers give the same keys, whatever order they are read in.
        assert sorted(i.key for i in again if i.classification == "riser") == sorted(risers)
        # Two drawn at the very same point are still two items, with keys that hold.
        twins = [replace(ungridded[0], id="twin-a"), replace(ungridded[0], id="twin-b")]
        doubled = [
            generate.generate([*others, *order], general.runs, spec, RULES, [CEILING])
            for order in (twins, list(reversed(twins)))
        ]
        twin_keys = [sorted(i.key for i in run if i.classification == "riser") for run in doubled]
        assert len(set(twin_keys[0])) == 2 and twin_keys[0] == twin_keys[1]
        # A riser alone at its grid reference keeps the key it always had.
        alone = generate.generate(general.detections, general.runs, spec, RULES, [CEILING])
        assert next(i.key for i in alone if i.classification == "riser") == before

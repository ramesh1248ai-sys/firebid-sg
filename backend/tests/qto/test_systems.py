"""The Phase 2 systems, from drawings to takeoff (FR-VIS-04, FR-QTO-06).

The synthetic tender of `synthetic_systems`: a pump room, a typical floor, a site plan and
a riser schematic. Every type of equipment is detected with its tag, and taken off once,
with what its schedule states of it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.evals import synthetic_systems as fixture
from firebid.qto.model import ItemDraft, SpecValue
from tests.qto.conftest import Tender, citation


class by_description:
    """The one item with a description. Fittings and hangers repeat on every level, so a
    description is looked up, not assumed to be the only one of its kind."""

    def __init__(self, items: list[ItemDraft]) -> None:
        self.items = items

    def __getitem__(self, description: str) -> ItemDraft:
        [found] = [item for item in self.items if item.description == description]
        return found

    def __contains__(self, description: str) -> bool:
        return any(item.description == description for item in self.items)


DUTY_PUMP = "Fire pump (duty, 2850 L/min, 80 m head, 75 kW)"
STANDBY_PUMP = "Fire pump (standby, 2850 L/min, 80 m head, 75 kW)"


@pytest.mark.req("FR-VIS-04")
class TestDetection:
    @pytest.mark.parametrize(
        "draw", [fixture.pump_room, fixture.floor_plan, fixture.site_plan, fixture.riser_schematic]
    )
    def test_every_type_on_a_sheet_is_found_with_the_exact_count(
        self, systems: Tender, draw: object
    ) -> None:
        _, truth = draw()  # type: ignore[operator]

        found: dict[str, int] = {}
        for detection in systems.detections:
            if detection.at.sheet_number == truth.number and detection.kind == "object":
                found[detection.object_type] = found.get(detection.object_type, 0) + 1

        assert found == truth.counts

    def test_all_thirteen_new_types_are_in_the_tender(self, systems: Tender) -> None:
        assert {d.object_type for d in systems.detections if d.kind == "object"} == {
            "fire_pump",
            "jockey_pump",
            "pump_controller",
            "fire_water_tank",
            "breeching_inlet",
            "landing_valve",
            "hydrant",
            "hose_reel",
            "test_header",
            "dry_pipe_valve_set",
            "pre_action_valve_set",
            "deluge_valve_set",
            "air_compressor",
        }

    def test_equipment_carries_the_tag_written_beside_it_and_the_words_as_evidence(
        self, systems: Tender
    ) -> None:
        tagged = {
            (d.at.sheet_number, str(d.attributes["tag"])): d
            for d in systems.detections
            if d.attributes.get("tag")
        }

        for draw in (fixture.pump_room, fixture.floor_plan, fixture.site_plan):
            _, truth = draw()
            for tag, kind in truth.tags.items():
                assert tagged[(truth.number, tag)].object_type == kind, tag
        assert tagged[(fixture.PUMP_ROOM, "FP-01")].evidence["tag"]["text"] == "FP-01"

    def test_pipe_is_measured_by_size_and_knows_its_system(self, systems: Tender) -> None:
        for draw in (fixture.pump_room, fixture.floor_plan, fixture.site_plan):
            _, truth = draw()
            lengths: dict[int, int] = {}
            for run in systems.runs:
                if run.at.sheet_number == truth.number and run.dn and run.length_mm:
                    lengths[run.dn] = lengths.get(run.dn, 0) + run.length_mm
            assert lengths == {dn: round(value) for dn, value in truth.lengths.items()}
        by_sheet = {run.at.sheet_number: run.system for run in systems.runs}
        assert by_sheet[fixture.SITE] == "hydrant"
        assert by_sheet[fixture.FLOOR] == "wet_riser"

    def test_a_schematic_gives_no_lengths(self, systems: Tender) -> None:
        on_schematic = [r for r in systems.runs if r.at.sheet_number == fixture.SCHEMATIC]

        assert on_schematic and all(run.length_mm is None for run in on_schematic)


@pytest.mark.req("FR-QTO-06")
class TestTakeoff:
    def test_each_type_is_counted_once_across_plans_and_schematic(self, systems: Tender) -> None:
        counted = {
            item.description: item.net_quantity
            for item in systems.items()
            if item.classification in ("equipment", "valve")
        }

        assert counted == {
            DUTY_PUMP: Decimal(1),
            STANDBY_PUMP: Decimal(1),
            "Jockey pump (90 L/min, 85 m head, 4 kW)": Decimal(1),
            "Pump controller": Decimal(1),
            "Fire water tank": Decimal(1),
            "Test header": Decimal(1),
            "Air compressor": Decimal(1),
            "Dry pipe valve set": Decimal(1),
            "Pre action valve set": Decimal(1),
            "Deluge valve set": Decimal(1),
            # On the floor plan; the schematic shows the same types and is not counted again.
            "Landing valve (DN100)": Decimal(1),
            "Hose reel": Decimal(2),
            "Hydrant": Decimal(3),
            # On no plan at all: counted from the schematic.
            "Breeching inlet": Decimal(1),
        }

    def test_a_pump_s_attributes_cite_its_schedule_row(self, systems: Tender) -> None:
        pump = by_description(systems.items())[DUTY_PUMP]

        assert pump.note == "tagged FP-01"
        flow = pump.attributes["flow_l_min"]
        assert (flow["value"], flow["source"]) == ("2850", "schedule")
        [cited] = flow["citations"]
        assert cited["sheet_number"] == fixture.PUMP_ROOM
        assert cited["schedule"] == fixture.SCHEDULE_HEADING
        assert cited["quote"] == "FP-01 | ELECTRIC FIRE PUMP | DUTY | 47.5 | 80 | 75"
        assert pump.attributes["head_m"]["value"] == "80"
        assert pump.sources[0]["sheet_number"] == fixture.PUMP_ROOM
        assert pump.calculation_method == "count" and pump.members

    def test_a_pump_is_not_given_the_size_of_its_pipe_but_a_landing_valve_is(
        self, systems: Tender
    ) -> None:
        items = by_description(systems.items())

        assert "nominal_diameter_mm" not in items[DUTY_PUMP].attributes
        assert items["Landing valve (DN100)"].attributes["nominal_diameter_mm"] == {
            "value": "100",
            "source": "drawing",
        }

    def test_with_no_schedule_equipment_is_still_counted_and_nothing_is_guessed(
        self, systems: Tender
    ) -> None:
        items = by_description(systems.items(schedules=[]))

        assert items["Fire pump"].net_quantity == 2
        assert items["Fire pump"].attributes == {}

    def test_the_breeching_inlet_is_kept_from_the_schematic(self, systems: Tender) -> None:
        [group] = [g for g in systems.groups if g.kind == "schematic"]

        kept = {m["object_type"] for m in group.members if m["keep"]}
        dropped = {m.get("object_type") for m in group.members if not m["keep"]}
        assert kept == {"breeching_inlet"}
        assert {"fire_pump", "landing_valve", "hose_reel"} <= dropped

    def test_the_rising_main_is_measured_by_the_riser_rule_from_the_level_schedule(
        self, systems: Tender
    ) -> None:
        riser = by_description(systems.items())["Rising main, DN100 (vertical, not drawn)"]

        assert riser.level == "L03" and riser.length_mm == fixture.FLOOR_TO_FLOOR_L03
        assert riser.rule is not None and riser.rule["rule_key"] == "riser_length"
        [height] = [i for i in riser.rule["inputs"] if i["name"] == "floor_to_floor_mm"]
        assert height["value"] == 4_500
        assert height["source"] == (
            f"level schedule on sheet {fixture.SCHEMATIC}: 'L03 FFL +9.000' to 'L04 FFL +13.500'"
        )

    def test_a_level_the_schedule_does_not_name_falls_back_to_the_rule_s_default(
        self, systems: Tender
    ) -> None:
        riser = by_description(systems.items())["Rising main, DN150 (vertical, not drawn)"]

        assert riser.level == "B1" and riser.length_mm == 4_000
        assert riser.rule is not None
        assert "rule default" in riser.rule["inputs"][0]["source"]

    def test_hydrant_pipe_is_measured_from_the_site_plan_with_the_hydrant_specification(
        self, systems: Tender
    ) -> None:
        def spec(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
            if system != "hydrant":
                return {}
            return {
                "pipe_material": [SpecValue("ductile_iron", "4.1", citation("4.1"))],
                "joining_method": [SpecValue("flanged", "4.1", citation("4.1"))],
            }

        items = by_description(systems.items(spec=spec))
        main = items["Pipe, DN150, main (ductile iron, flanged), hydrant system"]

        assert main.length_mm == 26_250 and main.net_quantity == Decimal("26.250")
        assert main.calculation_method == "centreline_length"
        assert main.attributes["system"]["value"] == "hydrant"
        assert main.attributes["pipe_material"]["citations"] == [citation("4.1")]
        assert {s["sheet_number"] for s in main.sources} == {fixture.SITE}

    def test_a_rising_main_is_specified_as_a_wet_or_dry_riser(self, systems: Tender) -> None:
        def spec(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
            if system != "wet_riser":
                return {}
            return {"pipe_material": [SpecValue("galvanised_steel", "5.1", citation("5.1"))]}

        items = by_description(systems.items(spec=spec))

        assert "Pipe, DN100, main (galvanised steel), wet rising main" in items

    def test_every_item_has_what_its_evidence_record_needs(self, systems: Tender) -> None:
        for item in systems.items():
            assert item.sources and item.members and item.calculation_method, item.description
            assert item.inputs_hash()

    def test_taking_off_twice_gives_the_same_items(self, systems: Tender) -> None:
        first = {item.key: item.inputs_hash() for item in systems.items()}
        second = {item.key: item.inputs_hash() for item in systems.items()}

        assert first == second and len(first) == len(systems.items())

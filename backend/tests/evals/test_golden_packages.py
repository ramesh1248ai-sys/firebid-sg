"""The committed golden reference packages (`docs/plan/TEST_STRATEGY.md`, section 4).

A synthetic package's expected values come from what its fixture generator draws. If the
generator changes, the package must change with it, in the same commit, or a comparison
would blame the platform for the fixture's change. These tests are that guard: they compare
a package with its generator, never with the platform's own reading of the drawings.
"""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from firebid.evals import synthetic_boq, synthetic_labour, synthetic_rates
from firebid.evals import synthetic_qto as q
from firebid.evals import synthetic_spec as spec
from firebid.evals import synthetic_systems as systems
from firebid.evals.synthetic_network import NETWORK, TYPES

pytestmark = pytest.mark.req("FR-LRN-01")

GOLDEN = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"
CONFIG = Path(__file__).resolve().parents[2] / "config"
COMPARISONS = {"EXACT", "SEMANTIC", "TOLERANCE", "EVIDENCE", "COMPLETENESS"}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def package(test_case: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (GOLDEN / test_case / "golden.json").read_text(encoding="utf-8")
    )
    return loaded


def stage(golden: dict[str, Any], stage_id: str) -> dict[str, Any]:
    (found,) = [one for one in golden["stages"] if one["stage_id"] == stage_id]
    expected: dict[str, Any] = found["expected_output"]
    return expected


@pytest.mark.parametrize("folder", sorted(path.name for path in GOLDEN.iterdir()))
def test_every_committed_package_has_its_three_files_and_a_well_formed_dataset(
    folder: str,
) -> None:
    for name in ("package.md", "golden.json", "manifest.json"):
        assert (GOLDEN / folder / name).is_file(), f"{folder} has no {name}"
    golden = package(folder)
    manifest = json.loads((GOLDEN / folder / "manifest.json").read_text(encoding="utf-8"))

    assert golden["test_case_id"] == manifest["test_case_id"] == folder
    assert manifest["kind"] == "synthetic", "a real tender's package is not committed"
    numbers = [one["stage_id"] for one in golden["stages"]]
    assert numbers == sorted(set(numbers)), "each stage once, in order"
    for one in golden["stages"]:
        assert one["stage_id"].startswith("STG-") and one["stage_name"]
        assert one["comparison_type"] in COMPARISONS
        assert one["severity_if_incorrect"] in SEVERITIES
        assert one["expected_output"] and one["mandatory_fields"]
    assert golden["final_output"]["expected_result"]


class TestTcSyn001:
    """The general arrangement, the enlarged plan and the riser schematic."""

    SHEETS = (
        ("FP-L05-201", q.general_arrangement),
        ("FP-L05-301", q.enlarged_plan),
        ("FP-SCH-001", q.riser_schematic),
    )

    def test_its_counts_and_positions_are_what_the_generator_draws(self) -> None:
        sheets = {one["sheet"]: one for one in stage(package("TC-SYN-001"), "STG-005")["sheets"]}

        assert list(sheets) == [number for number, _ in self.SHEETS]
        for number, make in self.SHEETS:
            drawn = make()[1]
            assert sheets[number]["counts"] == q.counted(drawn)
            placed = sorted(
                (TYPES[s.block], s.x, s.y) for s in drawn.symbols if TYPES[s.block] != "pipe"
            )
            listed = sorted(
                (one["object_type"], one["x_mm"], one["y_mm"])
                for one in sheets[number]["instances"]
            )
            assert listed == placed

    def test_its_pipe_lengths_are_what_the_generator_draws(self) -> None:
        sheets = {one["sheet"]: one for one in stage(package("TC-SYN-001"), "STG-006")["sheets"]}

        for number, make in self.SHEETS:
            lengths: dict[str, float] = {}
            for pipe in make()[1].pipes:
                size = str(pipe.dn)
                lengths[size] = lengths.get(size, 0.0) + math.hypot(
                    pipe.x1 - pipe.x0, pipe.y1 - pipe.y0
                )
            assert sheets[number]["length_mm_by_dn"] == pytest.approx(lengths)

    def test_its_legend_is_the_consultant_s(self) -> None:
        rows = stage(package("TC-SYN-001"), "STG-004")["rows"]

        assert [(row["block"], row["description"], row["object_type"]) for row in rows] == [
            (symbol.block, symbol.description, symbol.object_type) for symbol in NETWORK.symbols
        ]

    def test_its_takeoff_counts_the_installation_once(self) -> None:
        golden = package("TC-SYN-001")
        whole = q.counted(q.general_arrangement()[1])
        counted = {
            item["item"]: item["quantity"]
            for item in stage(golden, "STG-007")["drawn_items"]
            if item["unit"] == "no"
        }

        assert counted == whole
        final = golden["final_output"]["expected_result"]
        assert final["sprinklers_total"] == sum(
            count for kind, count in whole.items() if kind.startswith("sprinkler_")
        )
        # The trap the case is there to catch: the sheets added together are more.
        every = [q.counted(make()[1]) for _, make in self.SHEETS]
        assert final["sum_of_the_sheets_before_duplicates"] == {
            kind: sum(sheet.get(kind, 0) for sheet in every)
            for kind in ("sprinkler_pendent", "gate_valve", "check_valve")
        }


class TestTcSyn002:
    """The basement car park plan with its specification, client bill, rates and labour."""

    SHEET = "FP-B1-201"

    def test_its_counts_positions_and_pipe_are_what_the_generator_draws(self) -> None:
        golden = package("TC-SYN-002")
        drawn = q.car_park_plan(self.SHEET, spec.CAR_PARK_NOTES)[1]
        (objects,) = stage(golden, "STG-005")["sheets"]
        (pipes,) = stage(golden, "STG-006")["sheets"]

        assert objects["sheet"] == pipes["sheet"] == self.SHEET
        assert objects["counts"] == q.counted(drawn)
        assert sorted(
            (one["object_type"], one["x_mm"], one["y_mm"]) for one in objects["instances"]
        ) == sorted((TYPES[s.block], s.x, s.y) for s in drawn.symbols if TYPES[s.block] != "pipe")
        lengths: dict[str, float] = {}
        for pipe in drawn.pipes:
            size = str(pipe.dn)
            lengths[size] = lengths.get(size, 0.0) + math.hypot(
                pipe.x1 - pipe.x0, pipe.y1 - pipe.y0
            )
        assert pipes["length_mm_by_dn"] == pytest.approx(lengths)
        assert stage(golden, "STG-003")["general_notes"] == list(spec.CAR_PARK_NOTES)
        assert [
            (row["block"], row["description"], row["object_type"])
            for row in stage(golden, "STG-004")["rows"]
        ] == [(s.block, s.description, s.object_type) for s in NETWORK.symbols]

    def test_its_specification_answers_are_the_fixture_s(self) -> None:
        read = stage(package("TC-SYN-002"), "STG-008")

        assert [(c["number"], c["heading"], c["has_text"]) for c in read["clauses"]] == [
            (number, heading, bool(text)) for number, heading, text in spec.with_risks()
        ]
        assert [
            (
                a["system"],
                a["attribute"],
                a["value"],
                a["clause"],
                a["dn_min"],
                a["dn_max"],
                a["condition"],
            )
            for a in read["attributes"]
        ] == [
            (e.system, e.attribute, e.value, e.clause, e.dn_min, e.dn_max, e.condition)
            for e in spec.EXPECTED
        ]
        assert [(o["category"], o["clause"], o["quantities"]) for o in read["obligations"]] == [
            *spec.EXPECTED_OBLIGATIONS
        ]
        assert [(i["rule"], i["clause"], i["about"]) for i in read["issues"]] == [
            *spec.SEEDED_ISSUES
        ]
        assert [
            (row["key"], row["status"], row["clause"])
            for row in read["scope_matrix"]["interfaces_for_each_system"]
        ] == [*spec.EXPECTED_INTERFACES]

    def test_its_risks_are_the_fixture_s_and_the_basement_s(self) -> None:
        risks = stage(package("TC-SYN-002"), "STG-011")["risks"]
        cited = {risk["kind"]: tuple(risk.get("clauses", ())) for risk in risks}

        for kind, clauses in spec.EXPECTED_DESIGN_RISKS:
            assert cited[kind] == clauses
        for kind, clause in spec.EXPECTED_WORDING_RISKS:
            assert cited[kind] == (clause,)
        assert set(cited) == {kind for kind, _ in spec.EXPECTED_DESIGN_RISKS} | {
            kind for kind, _ in spec.EXPECTED_WORDING_RISKS
        } | {"basement"}

    def test_the_client_s_bill_and_its_mapping_are_the_fixture_s(self) -> None:
        compared = stage(package("TC-SYN-002"), "STG-009")
        lines = [line for _, _, section in synthetic_boq.SECTIONS for line in section]

        assert [
            (row["item"], row["description"], row["unit"], row["generator_says"])
            for row in compared["client_bill"]["lines"]
        ] == [(line.item, line.description, line.unit, line.maps_to) for line in lines]
        for row, line in zip(compared["client_bill"]["lines"], lines, strict=True):
            assert (row["maps_to"] is None) == (line.maps_to is None)
            if line.quantity is not None:
                assert Decimal(str(row["client_quantity"])) == line.quantity
        ours = {line["line"] for line in compared["our_bill"]["lines"]}
        assert {row["maps_to"] for row in compared["client_bill"]["lines"]} - {None} <= ours

    def test_every_price_and_every_hour_comes_from_the_two_lists(self) -> None:
        priced = stage(package("TC-SYN-002"), "STG-010")
        rates = {
            (f"{row.source_type} {row.source_reference}", row.rate) for row in synthetic_rates.RATES
        }
        entries = {
            (entry[3], str(entry[5]), entry[6], f"{entry[7]}: {entry[8]}")
            for entry in synthetic_labour.ENTRIES
        }

        for line in priced["lines"]:
            if line["price_status"] == "priced":
                assert (line["rate_source"], line["unit_rate"]) in rates
                amount = Decimal(str(line["quantity"])) * Decimal(line["unit_rate"])
                assert Decimal(line["amount"]) == amount.quantize(Decimal("0.01"), ROUND_HALF_UP)
            else:
                assert line["unit_rate"] is None and line["amount"] is None
            if (labour := line["labour"]) is not None:
                assert (
                    labour["productivity_entry"],
                    labour["hours_per_unit"],
                    labour["trade"],
                    labour["productivity_source"],
                ) in entries
                assert labour["hourly_rate"] == priced["labour_rates"]["trades"][labour["trade"]]

    def test_its_figures_add_up(self) -> None:
        golden = package("TC-SYN-002")
        priced = stage(golden, "STG-010")
        built = priced["build_up"]

        def amounts(*groups: str) -> Decimal:
            return sum(
                (
                    Decimal(line["amount"])
                    for line in priced["lines"]
                    if line["amount"] is not None and line["group"] in groups
                ),
                Decimal(0),
            )

        labour = sum(
            (Decimal(line["labour"]["cost"]) for line in priced["lines"] if line["labour"]),
            Decimal(0),
        )
        assert Decimal(built["materials"]) == amounts("Sprinkler heads", "Pipework")
        assert Decimal(built["fittings"]) == amounts("Fittings")
        assert Decimal(built["valves"]) == amounts("Valves and ancillaries")
        assert Decimal(built["labour"]) == labour == Decimal(priced["labour"]["cost"])
        direct = sum(
            (Decimal(built[k]) for k in ("materials", "fittings", "valves", "wastage", "labour")),
            Decimal(0),
        )
        assert Decimal(built["direct"]) == Decimal(built["cost"]) == direct
        excluding = direct + Decimal(built["margin"]["amount"])
        assert Decimal(built["total_excluding_gst"]) == excluding
        assert Decimal(built["total_including_gst"]) == excluding + Decimal(built["gst"])
        pack = stage(golden, "STG-012")
        final = golden["final_output"]["expected_result"]
        for figure in ("total_excluding_gst", "gst", "total_including_gst"):
            assert pack["figures"][figure] == built[figure] == final[figure]
        unpriced = [one["line"] for one in priced["lines"] if one["price_status"] != "priced"]
        assert sorted(priced["unpriced_lines"]) == sorted(unpriced)
        assert pack["unpriced_lines"] == final["unpriced_lines"] == len(unpriced)

    def test_the_rules_it_was_worked_out_with_are_still_the_configured_ones(self) -> None:
        used = package("TC-SYN-002")["business_rules_used"]

        def configured(name: str) -> dict[str, Any]:
            loaded: dict[str, Any] = yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))
            return loaded

        rules = {
            rule["key"]: rule["definition"]
            for rule in configured("measurement_rules.yaml")["rules"]
        }
        assert used["drop_length_defaults"] == rules["drop_length"]["defaults"]
        assert used["riser_length_defaults"] == rules["riser_length"]["defaults"]
        assert used["hanger_default_spacing_mm"] == rules["hanger_spacing"]["default_spacing_mm"]
        assert used["allowance_percent"] == rules["allowance"]["percent"]
        assert (
            used["variance_threshold_percent"]
            == (configured("boq.yaml")["variance_threshold_percent"])
        )
        assert used["gst_percent"] == configured("pricing.yaml")["gst"]["rates"][-1]["percent"]
        labour = configured("labour.yaml")
        (table,) = labour["rate_tables"]
        recorded = used["labour_rate_table"]
        assert recorded["effective_from"] == str(table["effective_from"])
        for key in ("productive_hours_per_month", "overtime", "supervision", "insurance_percent"):
            assert recorded[key] == table[key]
        assert recorded["grades"] == table["grades"]
        assert recorded["trades"] == {name: table["trades"][name] for name in recorded["trades"]}
        assert used["labour_multipliers"] == {
            one["key"]: str(one["value"]) for one in labour["multipliers"]["conditions"]
        }


class TestTcSyn003:
    """The pump room, the typical floor, the site plan and the riser schematic."""

    @staticmethod
    def truths() -> dict[str, systems.SheetTruth]:
        return {truth.number: truth for _, truth in systems.tender()}

    def test_its_counts_tags_and_pipe_are_what_the_generator_draws(self) -> None:
        golden = package("TC-SYN-003")
        objects = {one["sheet"]: one for one in stage(golden, "STG-005")["sheets"]}
        pipes = {one["sheet"]: one for one in stage(golden, "STG-006")["sheets"]}
        truths = self.truths()

        assert set(objects) == set(pipes) == set(truths)
        for number, truth in truths.items():
            assert objects[number]["counts"] == truth.counts
            assert objects[number]["tags"] == truth.tags
            listed: dict[str, int] = {}
            for one in objects[number]["instances"]:
                listed[one["object_type"]] = listed.get(one["object_type"], 0) + 1
                if "tag" in one:
                    assert truth.tags[one["tag"]] == one["object_type"]
            assert listed == truth.counts
            assert pipes[number]["measured"] is truth.measured
            assert pipes[number]["length_mm_by_dn"] == {
                str(dn): length for dn, length in truth.lengths.items()
            }
            drawn: dict[str, float] = {}
            for run in pipes[number]["runs"]:
                (x0, y0), (x1, y1) = run["from_mm"], run["to_mm"]
                drawn[str(run["dn"])] = drawn.get(str(run["dn"]), 0.0) + math.hypot(
                    x1 - x0, y1 - y0
                )
            assert drawn == pytest.approx(pipes[number]["length_mm_by_dn"])

    def test_its_legend_schedule_and_levels_are_the_consultant_s(self) -> None:
        golden = package("TC-SYN-003")
        views = stage(golden, "STG-003")

        assert [
            (row["block"], row["description"], row["object_type"])
            for row in stage(golden, "STG-004")["rows"]
        ] == [(s.block, s.description, s.object_type) for s in systems.DELTA.symbols]
        assert [
            (row["tag"], row["description"], row["flow_l_s"], row["head_m"], row["power_kw"])
            for row in golden["final_output"]["expected_result"]["pump_schedule"]
        ] == [(row[0], row[1], *row[3:]) for row in systems.SCHEDULE_ROWS]
        assert [(one["level"], one["ffl_m"]) for one in views["level_schedule"]["levels"]] == [
            *systems.LEVELS,
            systems.ROOF,
        ]
        assert views["level_schedule"]["floor_to_floor_mm"]["L03"] == systems.FLOOR_TO_FLOOR_L03

    def test_its_takeoff_counts_each_piece_of_equipment_once(self) -> None:
        golden = package("TC-SYN-003")
        truths = self.truths()
        takeoff = stage(golden, "STG-007")
        on_plans: dict[str, int] = {}
        every: dict[str, int] = {}
        for truth in truths.values():
            for kind, count in truth.counts.items():
                every[kind] = every.get(kind, 0) + count
                if truth.measured:
                    on_plans[kind] = on_plans.get(kind, 0) + count
        # What only the schematic shows is counted from it; what a plan shows is not.
        only = {k: v for k, v in truths[systems.SCHEMATIC].counts.items() if k not in on_plans}

        assert takeoff["counted_once"] == {**on_plans, **only}
        listed: dict[str, int] = {}
        for item in takeoff["equipment"]:
            listed[item["item"]] = listed.get(item["item"], 0) + item["quantity"]
        assert listed == takeoff["counted_once"]
        final = golden["final_output"]["expected_result"]
        assert final["counted_once"] == takeoff["counted_once"]
        # The trap the case is there to catch: the sheets added together are more.
        assert final["sum_of_the_sheets_before_duplicates"] == {
            kind: count for kind, count in every.items() if count != takeoff["counted_once"][kind]
        }

    def test_the_rules_it_was_worked_out_with_are_still_the_configured_ones(self) -> None:
        used = package("TC-SYN-003")["business_rules_used"]
        loaded = yaml.safe_load((CONFIG / "measurement_rules.yaml").read_text(encoding="utf-8"))
        rules = {rule["key"]: rule["definition"] for rule in loaded["rules"]}

        assert used["hanger_default_spacing_mm"] == rules["hanger_spacing"]["default_spacing_mm"]
        assert used["hanger_excluded_systems"] == rules["hanger_spacing"]["exclude_systems"]
        assert used["riser_length_defaults"] == rules["riser_length"]["defaults"]

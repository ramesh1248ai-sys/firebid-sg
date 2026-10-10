"""A run against a golden reference package: differences, their class and the score.

The runs here are made from a package's own expected outputs, changed where a test says.
Nothing of the platform is run: `tests/db/test_golden_run.py` does that.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from firebid.evals import golden
from firebid.evals.cli import main

pytestmark = pytest.mark.req("FR-LRN-01")

PACKAGES = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"
GOLDEN = ("--root", str(PACKAGES.parents[1]), "golden", "--case")
COMPARED = ("STG-001", "STG-002", "STG-003", "STG-004", "STG-005", "STG-006", "STG-007")


@pytest.fixture(scope="module")
def package() -> golden.Package:
    return golden.load(PACKAGES / "TC-SYN-001")


def the_reference_s_own(package: golden.Package) -> golden.Run:
    """A run that gives exactly what the package expects."""
    return {
        stage.stage_id: copy.deepcopy(stage.expected_output)
        for stage in package.stages
        if stage.stage_id in COMPARED
    }


def sheet(run: golden.Run, stage_id: str, number: str) -> dict[str, Any]:
    found: dict[str, Any]
    (found,) = [one for one in run[stage_id]["sheets"] if one["sheet"] == number]
    return found


def defects(result: golden.Result) -> list[tuple[str, str, str, str]]:
    return [
        (d.stage_id, d.what, d.kind, d.classification) for d in result.differences if d.is_defect
    ]


@pytest.mark.parametrize("folder", sorted(path.name for path in PACKAGES.iterdir()))
def test_every_committed_package_is_read_and_agrees_with_itself(folder: str) -> None:
    package = golden.load(PACKAGES / folder)
    run = {stage.stage_id: copy.deepcopy(stage.expected_output) for stage in package.stages}

    result = golden.compare(package, run)

    assert result.differences == []
    # Every stage the package has is compared, and none passes on no checks at all.
    assert [s.status for s in result.stages] == ["compared"] * len(package.stages)
    assert all(s.checks > 0 for s in result.stages)


def test_a_run_that_gives_the_reference_scores_full_marks_on_what_was_measured(
    package: golden.Package,
) -> None:
    result = golden.compare(package, the_reference_s_own(package))

    scored = golden.score(result)

    assert scored.overall == 1.0
    measured = {d.key: d.score for d in scored.dimensions}
    assert measured["evidence"] == 1.0 and measured["final_output"] is None
    # 100% of the 95% that this package measures (it stops at stage 7), and the report says
    # which 95%.
    assert scored.measured_weight == 95
    assert "over the 95% of the weights that were measured" in golden.report(result, scored)


def test_a_wrong_count_is_a_critical_defect_at_the_stage_it_first_shows(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    # The enlarged plan's heads counted again, on top of the general arrangement's.
    sheet(run, "STG-005", "FP-L05-201")["counts"]["sprinkler_pendent"] = 20
    (pendents,) = [i for i in run["STG-007"]["drawn_items"] if i["item"] == "sprinkler_pendent"]
    pendents["quantity"] = 20

    result = golden.compare(package, run)

    assert defects(result) == [
        ("STG-005", "fp-l05-201 / sprinkler_pendent / count", "wrong", "CRITICAL"),
        ("STG-007", "sprinkler_pendent / no", "wrong", "CRITICAL"),
    ]
    assert result.first_stage_that_differs() == "STG-005"
    assert result.defects() == {"CRITICAL": 2, "HIGH": 0, "MEDIUM": 0, "LOW": 0}


@pytest.mark.parametrize(("length", "wrong"), [(74_800.0, False), (76_000.0, True)])
def test_a_pipe_length_is_wrong_only_beyond_five_percent(
    package: golden.Package, length: float, wrong: bool
) -> None:
    run = the_reference_s_own(package)
    sheet(run, "STG-006", "FP-L05-201")["length_mm_by_dn"]["50"] = length  # 72,000 expected

    found = defects(golden.compare(package, run))

    assert found == (
        [("STG-006", "fp-l05-201 / DN50 / length mm", "wrong", "HIGH")] if wrong else []
    )


def test_a_difference_on_a_recorded_ambiguity_is_to_settle_and_is_not_scored(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    # A1: a drop for every head, not only the pendents: 12.0 m against 8.0 m.
    (drops,) = [i for i in run["STG-007"]["derived_items"] if i.get("run") == "drop"]
    drops["quantity"] = 12.0

    result = golden.compare(package, run)

    (one,) = result.differences
    assert (one.classification, one.ambiguity, one.expected, one.actual) == (
        "TO SETTLE",
        "A1",
        8.0,
        12.0,
    )
    assert not one.is_defect and golden.score(result).overall == 1.0
    assert "TO SETTLE (A1)" in golden.report(result, golden.score(result))


def test_what_the_reference_has_and_the_run_has_not_is_missing(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    run["STG-004"]["rows"] = [
        row for row in run["STG-004"]["rows"] if row["description"] != "SIDEWALL SPRINKLER"
    ]

    result = golden.compare(package, run)

    assert defects(result) == [
        ("STG-004", "sidewall sprinkler / object type", "missing", "CRITICAL")
    ]
    completeness = next(d for d in golden.score(result).dimensions if d.key == "completeness")
    assert (completeness.checks, completeness.passed) == (8, 7)


def test_what_only_the_run_has_is_listed_for_review_and_not_scored(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    # A flow switch is in the legend and installed nowhere.
    sheet(run, "STG-005", "FP-L05-201")["counts"]["flow_switch"] = 1

    result = golden.compare(package, run)

    (one,) = result.differences
    assert (one.kind, one.classification, one.actual) == ("extra", "FOR REVIEW", 1)
    assert golden.score(result).overall == 1.0
    assert "## For review" in golden.report(result, golden.score(result))


def test_a_lesser_fact_takes_a_lesser_class_than_its_stage(package: golden.Package) -> None:
    run = the_reference_s_own(package)
    (first, *_rest) = run["STG-002"]["sheets"]
    first["title"] = "LEVEL 5 SPRINKLER LAYOUT"  # the word PLAN lost
    first["revision"] = "R02"

    found = defects(golden.compare(package, run))

    assert found == [
        ("STG-002", "fp-l05-201 / revision", "wrong", "HIGH"),
        ("STG-002", "fp-l05-201 / title", "wrong", "LOW"),
    ]


def test_case_and_spacing_are_not_differences(package: golden.Package) -> None:
    run = the_reference_s_own(package)
    (first, *_rest) = run["STG-002"]["sheets"]
    first["drawing_number"] = "fp-l05-201"
    first["title"] = "Level 5  Sprinkler Layout Plan"

    assert golden.compare(package, run).differences == []


def test_a_stage_the_run_does_not_have_is_not_exported_and_has_not_passed(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    del run["STG-003"]

    result = golden.compare(package, run)

    (views,) = [s for s in result.stages if s.stage_id == "STG-003"]
    assert (views.status, views.checks) == ("not exported", 0)
    assert "**not exported**" in golden.report(result, golden.score(result))


def test_the_same_item_at_another_size_is_one_difference_of_size(
    package: golden.Package,
) -> None:
    run = the_reference_s_own(package)
    (reducer,) = [i for i in run["STG-007"]["drawn_items"] if i.get("fitting") == "reducer"]
    reducer["dn"] = 150  # the outlet size lost: 150 x 100 expected
    (main,) = [i for i in run["STG-007"]["drawn_items"] if i["item"] == "pipe" and i["dn"] == 100]
    main["dn"] = 80  # the DN100 main read as DN80, at the same length

    result = golden.compare(package, run)

    assert [(d.what, d.expected, d.actual, d.classification) for d in result.differences] == [
        ("fitting / reducer / no / size", "DN150x100", "DN150", "MEDIUM"),
        ("pipe / main / m / size", "DN100", "DN80", "HIGH"),
    ]


def test_an_item_that_is_not_there_at_any_size_is_missing(package: golden.Package) -> None:
    run = the_reference_s_own(package)
    run["STG-007"]["drawn_items"] = [
        i for i in run["STG-007"]["drawn_items"] if i.get("fitting") != "reducer"
    ]

    assert defects(golden.compare(package, run)) == [
        ("STG-007", "fitting / reducer / DN150x100 / no", "missing", "CRITICAL")
    ]


def test_drawn_pipe_is_compared_by_size_alone_where_the_reference_names_no_run() -> None:
    package = golden.load(PACKAGES / "TC-SYN-003")
    run = the_reference_s_own(package)
    # As the platform gives it: one line a level, each called a main.
    for item in run["STG-007"]["pipe"]:
        item["run"] = "main"

    assert golden.compare(package, run).differences == []

    run["STG-007"]["pipe"][0]["quantity"] += 3.0
    assert defects(golden.compare(package, run)) == [
        ("STG-007", "pipe / DN200 / m", "wrong", "CRITICAL")
    ]


def test_an_equal_tee_is_the_same_tee_however_its_size_is_written() -> None:
    package = golden.load(PACKAGES / "TC-SYN-003")
    run = the_reference_s_own(package)
    for item in run["STG-007"]["derived_items"]:
        if item.get("fitting") == "tee" and item["dn"] == "150x150":
            item["dn"] = 150

    assert golden.compare(package, run).differences == []


class TestLevelsAtTheTakeoff:
    @pytest.fixture(scope="class")
    def systems(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-003")

    def test_an_item_on_another_level_is_a_high_defect_of_its_level_alone(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        for item in run["STG-007"]["equipment"]:
            if item["item"] == "landing_valve":
                item["level"] = "L04"

        assert defects(golden.compare(systems, run)) == [
            ("STG-007", "landing_valve / DN100 / no / level", "wrong", "HIGH")
        ]

    def test_an_item_the_reference_gives_no_level_is_to_have_none(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        for item in run["STG-007"]["equipment"]:
            if item["item"] == "hydrant":
                item["level"] = "SITE"

        (found,) = golden.compare(systems, run).differences

        assert (found.what, found.expected, found.actual) == (
            "hydrant / no / level",
            "no level",
            "site",
        )

    def test_a_level_stated_once_for_the_takeoff_is_every_item_s(
        self, package: golden.Package
    ) -> None:
        run = the_reference_s_own(package)
        assert run["STG-007"].pop("level") == "L05"
        for part in ("drawn_items", "derived_items"):
            for item in run["STG-007"][part]:
                item["level"] = "L05"

        assert golden.compare(package, run).differences == []

        run["STG-007"]["derived_items"][0]["level"] = "L06"
        (found,) = golden.compare(package, run).differences
        assert found.what.endswith("/ level") and found.classification == "HIGH"

    def test_an_item_on_two_levels_is_compared_level_by_level(
        self, systems: golden.Package
    ) -> None:
        """The DN150 pipe is the pump room's and the site main's. Three metres moved from
        one to the other leave the total and the levels as they were: a level's labour
        multiplier is on that level's share, so the share is compared."""
        run = the_reference_s_own(systems)
        pipe = [i for i in run["STG-007"]["pipe"] if i["dn"] == 150]
        assert sorted(str(i["level"]) for i in pipe) == ["B1", "None"]
        for item in pipe:
            item["quantity"] += 3.0 if item["level"] == "B1" else -3.0

        found = golden.compare(systems, run).differences

        assert [(d.what, d.kind, d.classification) for d in found] == [
            ("pipe / DN150 / m / on b1", "wrong", "HIGH"),
            ("pipe / DN150 / m / on no level", "wrong", "HIGH"),
        ]
        assert (found[0].expected, found[0].actual) == (19.8, 22.8)

    def test_a_share_is_measured_as_the_length_is(self, systems: golden.Package) -> None:
        run = the_reference_s_own(systems)
        for item in run["STG-007"]["pipe"]:
            if item["dn"] == 150:
                item["quantity"] += 0.5 if item["level"] == "B1" else -0.5

        assert golden.compare(systems, run).differences == []

    def test_all_of_it_on_the_site_s_level_is_the_other_reading_of_c8(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        for part in ("pipe", "derived_items"):
            for item in run["STG-007"][part]:
                if item.get("level", "") is None and item["item"] in ("pipe", "fitting"):
                    item["level"] = "SITE"

        found = golden.compare(systems, run).differences

        assert {(d.what, d.ambiguity, d.other_reading) for d in found} == {
            ("pipe / DN150 / m / level", "C8", True),
            ("pipe / DN150 / m / on no level", "C8", True),
            ("fitting / tee / DN150x150 / no / level", "C8", True),
            ("fitting / tee / DN150x150 / no / on no level", "C8", True),
        }

    def test_an_item_on_one_level_has_no_share_to_compare(self, systems: golden.Package) -> None:
        stage = next(s for s in systems.stages if s.stage_id == "STG-007")
        shares = [
            fact.label()
            for fact in golden.EXTRACTORS["STG-007"](stage.expected_output, stage)
            if fact.key[-1].startswith("on ")
        ]

        assert shares == [
            "pipe / DN150 / m / on b1",
            "pipe / DN150 / m / on no level",
            "fitting / tee / DN150x150 / no / on b1",
            "fitting / tee / DN150x150 / no / on no level",
        ]

    def test_a_missing_item_is_reported_once_and_not_for_its_level_too(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        run["STG-007"]["equipment"] = [
            item for item in run["STG-007"]["equipment"] if item["item"] != "test_header"
        ]
        run["STG-007"]["equipment"].append(
            {"item": "flow_switch", "quantity": 1, "unit": "no", "level": "B1"}
        )

        assert [(d.what, d.kind) for d in golden.compare(systems, run).differences] == [
            ("test_header / no", "missing"),
            ("flow_switch / no", "extra"),
        ]


class TestAttributesAndSystemsAtTheTakeoff:
    """What an item states beyond its size, and the system it is of. On TC-SYN-003 (pumps
    with a schedule, pipe of three systems) and TC-SYN-002 (a specification nobody has
    verified, so nothing is to state a material)."""

    @pytest.fixture(scope="class")
    def systems(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-003")

    @pytest.fixture(scope="class")
    def priced(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-002")

    @staticmethod
    def pump(run: golden.Run, tag: str) -> dict[str, Any]:
        found: dict[str, Any]
        (found,) = [one for one in run["STG-007"]["equipment"] if one.get("tag") == tag]
        return found

    @staticmethod
    def pipe(run: golden.Run, sheet: str, dn: int) -> dict[str, Any]:
        found: dict[str, Any]
        (found,) = [
            one for one in run["STG-007"]["pipe"] if one["sheet"] == sheet and one["dn"] == dn
        ]
        return found

    def test_a_pump_of_another_duty_is_a_high_defect_of_that_attribute_alone(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        self.pump(run, "FP-02")["attributes"]["duty"] = "duty"

        result = golden.compare(systems, run)

        assert defects(result) == [("STG-007", "fire_pump / no / duty", "wrong", "HIGH")]
        # The two pumps are one kind of item: the values they have between them.
        (one,) = result.differences
        assert (one.expected, one.actual) == ("duty, standby", "duty")

    def test_an_attribute_an_item_does_not_state_is_not_specified(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        for tag in ("FP-01", "FP-02"):
            del self.pump(run, tag)["attributes"]["driver"]

        result = golden.compare(systems, run)

        assert defects(result) == [("STG-007", "fire_pump / no / driver", "wrong", "HIGH")]
        assert result.differences[0].actual == "not specified"

    def test_a_value_where_none_is_to_be_is_critical(self, priced: golden.Package) -> None:
        # Nobody has verified the specification: no pipe is to say what it is made of.
        run = the_reference_s_own(priced)
        (main,) = [
            one
            for one in run["STG-007"]["drawn_items"]
            if one["item"] == "pipe" and one["dn"] == 150
        ]
        main["attributes"] = {"pipe_material": "black_steel", "joining_method": "not specified"}

        result = golden.compare(priced, run)

        assert [d for d in defects(result) if d[0] == "STG-007"] == [
            ("STG-007", "pipe / DN150 / main / m / pipe_material", "wrong", "CRITICAL")
        ]
        (one,) = [d for d in result.differences if d.stage_id == "STG-007"]
        assert (one.expected, one.actual) == ("not specified", "black steel")

    def test_every_pipe_and_every_head_of_the_car_park_is_checked_for_it(
        self, priced: golden.Package
    ) -> None:
        own = {s.stage_id: copy.deepcopy(s.expected_output) for s in priced.stages}

        (takeoff,) = [s for s in golden.compare(priced, own).stages if s.stage_id == "STG-007"]
        bare = golden.compare(
            priced.model_copy(
                update={
                    "stages": tuple(
                        s.model_copy(
                            update={
                                "expected_output": {
                                    k: v for k, v in s.expected_output.items() if k != "attributes"
                                }
                            }
                        )
                        if s.stage_id == "STG-007"
                        else s
                        for s in priced.stages
                    )
                }
            ),
            own,
        )
        (without,) = [s for s in bare.stages if s.stage_id == "STG-007"]

        # Five kinds of pipe by three attributes, three kinds of head by four.
        assert takeoff.checks - without.checks == 5 * 3 + 3 * 4
        assert takeoff.passed == takeoff.checks

    def test_a_system_is_the_same_by_its_key_or_by_its_name(self, systems: golden.Package) -> None:
        run = the_reference_s_own(systems)
        self.pipe(run, "FP-L03-401", 100)["system"] = "wet_riser"
        self.pipe(run, "FP-SITE-001", 150)["system"] = "Hydrant system"

        assert golden.compare(systems, run).differences == []

    def test_pipe_put_with_another_system_is_a_medium_defect(self, systems: golden.Package) -> None:
        run = the_reference_s_own(systems)
        self.pipe(run, "FP-L03-401", 100)["system"] = "hydrant"
        stated_none = the_reference_s_own(systems)
        del self.pipe(stated_none, "FP-L03-401", 100)["system"]

        assert defects(golden.compare(systems, run)) == [
            ("STG-007", "pipe / DN100 / m / system", "wrong", "MEDIUM")
        ]
        (one,) = golden.compare(systems, stated_none).differences
        assert (one.what, one.actual, one.classification) == (
            "pipe / DN100 / m / system",
            "none stated",
            "MEDIUM",
        )

    def test_the_pump_room_s_pipe_as_the_rising_main_s_is_the_other_reading_of_c6(
        self, systems: golden.Package
    ) -> None:
        other = the_reference_s_own(systems)
        third = the_reference_s_own(systems)
        for dn in (200, 150, 50):
            self.pipe(other, "FP-B1-101", dn)["system"] = "wet_riser"
            self.pipe(third, "FP-B1-101", dn)["system"] = "sprinkler"

        settled = golden.compare(systems, other).differences
        wrong = golden.compare(systems, third)

        assert {(d.what, d.classification, d.ambiguity, d.other_reading) for d in settled} == {
            (f"pipe / DN{dn} / m / system", "TO SETTLE", "C6", True) for dn in (200, 150, 50)
        }
        assert defects(wrong) == [
            ("STG-007", f"pipe / DN{dn} / m / system", "wrong", "MEDIUM") for dn in (200, 150, 50)
        ]

    def test_what_the_package_does_not_state_is_not_compared(self, package: golden.Package) -> None:
        # TC-SYN-001 states no attribute and no system: a run that does is not marked for it.
        run = the_reference_s_own(package)
        for item in run["STG-007"]["drawn_items"]:
            item["attributes"] = {"pipe_material": "black_steel"}
            item["system"] = "sprinkler"

        assert golden.compare(package, run).differences == []

    def test_a_missing_item_is_reported_once_and_not_for_what_it_states_too(
        self, systems: golden.Package
    ) -> None:
        run = the_reference_s_own(systems)
        run["STG-007"]["equipment"] = [
            one for one in run["STG-007"]["equipment"] if one["item"] != "jockey_pump"
        ]

        assert defects(golden.compare(systems, run)) == [
            ("STG-007", "jockey_pump / no", "missing", "CRITICAL")
        ]


class TestEvidence:
    """What a value cites: a sheet, a clause, a source. On TC-SYN-003 (the sheets of a takeoff)
    and TC-SYN-002 (the clauses and the sources of a priced bid)."""

    @pytest.fixture(scope="class")
    def systems(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-003")

    @pytest.fixture(scope="class")
    def priced(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-002")

    @staticmethod
    def own(package: golden.Package) -> golden.Run:
        return {s.stage_id: copy.deepcopy(s.expected_output) for s in package.stages}

    @staticmethod
    def line(run: golden.Run, name: str) -> dict[str, Any]:
        found: dict[str, Any]
        (found,) = [one for one in run["STG-010"]["lines"] if one["line"] == name]
        return found

    def test_an_item_taken_off_from_another_sheet_is_a_medium_defect_of_evidence(
        self, systems: golden.Package
    ) -> None:
        run = self.own(systems)
        for item in run["STG-007"]["equipment"]:
            if item["item"] == "fire_water_tank":
                item["sheet"] = "FP-SCH-002"

        result = golden.compare(systems, run)

        assert defects(result) == [("STG-007", "fire_water_tank / no / sheet", "wrong", "MEDIUM")]
        (one,) = result.differences
        assert (one.expected, one.actual, one.dimension) == ("fp-b1-101", "fp-sch-002", "evidence")
        # The count is right, and is scored as right: the evidence alone is marked down.
        scored = {d.key: d.score for d in golden.score(result).dimensions}
        assert scored["calculation"] == 1.0
        assert scored["evidence"] is not None and scored["evidence"] < 1.0

    def test_an_item_is_from_every_sheet_any_part_of_it_is_from(
        self, systems: golden.Package
    ) -> None:
        # The package has the DN150 pipe from two sheets; a run that names them the other way
        # round, or in one item, cites the same two.
        run = self.own(systems)
        pipe = [one for one in run["STG-007"]["pipe"] if one["dn"] == 150]
        assert len(pipe) == 2
        for one in pipe:
            one["sheets"] = [one.pop("sheet")]
        pipe[0]["sheets"], pipe[1]["sheets"] = pipe[1]["sheets"], pipe[0]["sheets"]
        pipe[0]["level"], pipe[1]["level"] = pipe[1]["level"], pipe[0]["level"]
        pipe[0]["quantity"], pipe[1]["quantity"] = pipe[1]["quantity"], pipe[0]["quantity"]

        assert golden.compare(systems, run).differences == []

    def test_a_value_that_cites_nothing_is_missing_evidence_and_not_a_missing_value(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        del self.line(run, "heads_pendent")["rate_source"]

        result = golden.compare(priced, run)

        assert defects(result) == [
            ("STG-010", "line / heads_pendent / rate source", "missing", "MEDIUM")
        ]
        assert result.differences[0].dimension == "evidence"

    def test_a_rate_from_another_source_and_hours_from_another_entry_are_each_a_defect(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        line = self.line(run, "heads_pendent")
        line["rate_source"] = "quotation Q-2026-1001"
        line["labour"]["productivity_source"] = "estimator judgement: Sam Senior"

        assert defects(golden.compare(priced, run)) == [
            ("STG-010", "line / heads_pendent / rate source", "wrong", "MEDIUM"),
            ("STG-010", "line / heads_pendent / productivity source", "wrong", "MEDIUM"),
        ]

    def test_a_source_is_the_same_however_its_kind_is_spelt(self, priced: golden.Package) -> None:
        run = self.own(priced)
        self.line(run, "heads_pendent")["rate_source"] = "company_standard  cs-2026"

        assert golden.compare(priced, run).differences == []

    def test_a_risk_or_a_scope_row_from_another_clause_and_an_issue_on_another_sheet(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        (risk,) = [one for one in run["STG-011"]["risks"] if one["kind"] == "shop_drawings"]
        risk["clauses"] = ["6.5"]
        run["STG-008"]["issues"][0]["sheet"] = "FP-B1-202"
        matrix = run["STG-008"]["scope_matrix"]
        matrix["interfaces_for_each_system"][0]["clause"] = "7.9"

        found = defects(golden.compare(priced, run))

        assert ("STG-011", "risk / shop_drawings / clauses", "wrong", "MEDIUM") in found
        assert (
            "STG-008",
            "issue / conflict:pipe_material / clause 2.1.3 / sheet",
            "wrong",
            "MEDIUM",
        ) in found
        # The row is one of every system's, so its clause is wrong for each.
        assert {what for _, what, _, _ in found if what.endswith("/ clause")} == {
            f"scope / {system} / power_supply / clause" for system in matrix["systems"]
        }
        assert len(found) == 2 + len(matrix["systems"])

    def test_something_missing_is_reported_once_and_not_for_its_evidence_too(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        run["STG-011"]["risks"] = [r for r in run["STG-011"]["risks"] if r["kind"] != "shutdown"]

        result = golden.compare(priced, run)

        assert {d.what for d in result.differences} == {
            "risk / shutdown / proposed treatment",
            "risk / shutdown / impact",
        }
        assert not any(d.dimension == "evidence" for d in result.differences)

    def test_evidence_the_package_does_not_give_is_not_compared(
        self, package: golden.Package
    ) -> None:
        # TC-SYN-001 names no sheet for its items: a run that does is not marked for it.
        run = the_reference_s_own(package)
        for item in run["STG-007"]["drawn_items"]:
            item["sheets"] = ["FP-L05-201"]

        result = golden.compare(package, run)

        assert result.differences == []
        assert not any(d == "evidence" for d, _ in result.stages[6].outcomes)

    def test_a_citation_a_reviewer_accepts_as_equivalent_passes(
        self, priced: golden.Package
    ) -> None:
        def accepting(what: str) -> golden.Package:
            stages = []
            for stage in priced.stages:
                if stage.stage_id == "STG-010":
                    output = copy.deepcopy(stage.expected_output)
                    output["equivalent_evidence"] = [
                        {"what": what, "accepted": ["company standard CS-2026 rev A"]}
                    ]
                    stage = stage.model_copy(update={"expected_output": output})
                stages.append(stage)
            return priced.model_copy(update={"stages": tuple(stages)})

        run = self.own(priced)
        self.line(run, "heads_pendent")["rate_source"] = "Company standard CS-2026 rev A"
        other = self.own(priced)
        self.line(other, "heads_pendent")["rate_source"] = "company standard CS-2025"
        accepted = accepting("line / heads_pendent / rate source")

        assert defects(golden.compare(priced, run)) != []
        assert golden.compare(accepted, run).differences == []
        # Its own citation still passes, and one that is neither is still a defect.
        assert golden.compare(accepted, self.own(priced)).differences == []
        assert defects(golden.compare(accepted, other)) == [
            ("STG-010", "line / heads_pendent / rate source", "wrong", "MEDIUM")
        ]
        with pytest.raises(ValueError, match="no expected evidence is labelled"):
            golden.compare(accepting("line / heads_pendent / amount"), run)


class TestTheLaterStages:
    """Stages 8 to 12, on TC-SYN-002: the specification, the bills, the price, the risks and
    the review pack."""

    @pytest.fixture(scope="class")
    def priced(self) -> golden.Package:
        return golden.load(PACKAGES / "TC-SYN-002")

    @staticmethod
    def own(package: golden.Package) -> golden.Run:
        return {s.stage_id: copy.deepcopy(s.expected_output) for s in package.stages}

    def test_a_wrong_total_is_critical_and_first_shows_where_it_is_made(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        run["STG-010"]["build_up"]["total_excluding_gst"] = "6400.00"
        run["STG-012"]["figures"]["total_excluding_gst"] = "6400.00"

        result = golden.compare(priced, run)

        assert defects(result) == [
            ("STG-010", "build-up / total_excluding_gst", "wrong", "CRITICAL"),
            ("STG-012", "figure / total_excluding_gst", "wrong", "CRITICAL"),
        ]
        assert result.first_stage_that_differs() == "STG-010"

    def test_the_other_reading_of_an_ambiguity_is_to_settle_and_a_third_value_a_defect(
        self, priced: golden.Package
    ) -> None:
        other = self.own(priced)
        other["STG-012"]["figures"]["total_including_gst"] = "7035.29"
        third = self.own(priced)
        third["STG-012"]["figures"]["total_including_gst"] = "7000.00"

        settled = golden.compare(priced, other).differences
        wrong = golden.compare(priced, third)

        assert [(d.classification, d.ambiguity, d.other_reading) for d in settled] == [
            ("TO SETTLE", "B1", True)
        ]
        assert defects(wrong) == [("STG-012", "figure / total_including_gst", "wrong", "CRITICAL")]
        assert "B1: the other reading" in golden.report(
            golden.compare(priced, other), golden.score(golden.compare(priced, other))
        )

    def test_a_labour_cost_may_be_a_cent_out_and_no_more(self, priced: golden.Package) -> None:
        def with_cost(cost: str) -> golden.Result:
            run = self.own(priced)
            (line,) = [x for x in run["STG-010"]["lines"] if x["line"] == "heads_pendent"]
            line["labour"]["cost"] = cost
            return golden.compare(priced, run)

        assert with_cost("128.51").differences == []
        assert defects(with_cost("128.52")) == [
            ("STG-010", "line / heads_pendent / labour cost", "wrong", "CRITICAL")
        ]

    def test_a_bill_line_is_known_by_what_it_is_of_not_by_its_wording(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        for line in run["STG-009"]["our_bill"]["lines"]:
            line["description"] = "worded otherwise"
        run["STG-009"]["our_bill"]["lines"].reverse()

        assert golden.compare(priced, run).differences == []

    def test_an_issue_or_a_risk_the_run_does_not_have_is_missing(
        self, priced: golden.Package
    ) -> None:
        run = self.own(priced)
        run["STG-008"]["issues"] = run["STG-008"]["issues"][1:]
        run["STG-011"]["risks"] = [r for r in run["STG-011"]["risks"] if r["kind"] != "shutdown"]

        found = defects(golden.compare(priced, run))

        assert (
            "STG-008",
            "issue / conflict:pipe_material / clause 2.1.3",
            "missing",
            "HIGH",
        ) in found
        assert ("STG-011", "risk / shutdown / proposed treatment", "missing", "HIGH") in found

    def test_an_alternative_for_a_value_the_package_does_not_have_is_refused(
        self, priced: golden.Package
    ) -> None:
        stages = []
        for stage in priced.stages:
            if stage.stage_id == "STG-012":
                output = copy.deepcopy(stage.expected_output)
                output["alternatives"][0]["values"]["figure / no such figure"] = 1.0
                stage = stage.model_copy(update={"expected_output": output})
            stages.append(stage)
        broken = priced.model_copy(update={"stages": tuple(stages)})

        with pytest.raises(ValueError, match="no expected value is labelled"):
            golden.compare(broken, self.own(priced))


class TestCommand:
    """`firebid-eval golden`."""

    def run_file(self, tmp_path: Path, run: golden.Run) -> Path:
        path = tmp_path / "run.json"
        path.write_text(json.dumps({"bid": "a bid", "stages": run}), encoding="utf-8")
        return path

    def test_it_writes_the_report_and_passes_a_run_with_no_serious_defect(
        self, package: golden.Package, tmp_path: Path
    ) -> None:
        run = self.run_file(tmp_path, the_reference_s_own(package))
        report = tmp_path / "out" / "TC-SYN-001.md"

        code = main([*GOLDEN, "TC-SYN-001", "--run", str(run), "--report", str(report)])

        assert code == 0
        assert "# Golden reference comparison: TC-SYN-001" in report.read_text(encoding="utf-8")

    def test_a_critical_defect_fails_it(self, package: golden.Package, tmp_path: Path) -> None:
        changed = the_reference_s_own(package)
        sheet(changed, "STG-005", "FP-L05-201")["counts"]["sprinkler_pendent"] = 20
        run = self.run_file(tmp_path, changed)

        code = main([*GOLDEN, "TC-SYN-001", "--run", str(run)])

        assert code == 1

    def test_a_case_with_no_package_fails_and_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run = self.run_file(tmp_path, {})

        code = main([*GOLDEN, "TC-NONE", "--run", str(run)])

        assert code == 1 and "no golden reference package TC-NONE" in capsys.readouterr().out

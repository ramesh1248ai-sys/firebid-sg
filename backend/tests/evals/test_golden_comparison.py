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
    assert measured["evidence"] is None and measured["final_output"] is None
    # 100% of the 85% that can be measured so far, and the report says which 85%.
    assert scored.measured_weight == 85
    assert "over the 85% of the weights that were measured" in golden.report(result, scored)


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

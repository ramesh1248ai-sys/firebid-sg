"""The `p2_systems` evaluation suite: the Phase 2 equipment scored against truth, and gated.

On the synthetic tender every type is counted exactly and every pipe measured; a golden set
filed under `eval/truth/p2_systems` is read instead when there is one; and `compare` fails
when equipment detection gets worse than its accepted baseline.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from firebid.evals import metrics, p1_detection, p2_systems
from firebid.evals.cli import main
from firebid.evals.prediction import CountPrediction, SheetPrediction, TenderPrediction
from firebid.evals.runner import Baseline, compare, run_suite
from firebid.evals.schema import ObjectType

pytestmark = [pytest.mark.req("FR-VIS-04"), pytest.mark.req("FR-QTO-06")]


def test_the_synthetic_tender_is_counted_and_measured_exactly(tmp_path: Path) -> None:
    suite = p2_systems.generate(tmp_path)

    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite), suite="p2_systems")

    assert result.overall["equipment_count_accuracy"] == 1.0
    assert result.overall["pipe_length_error"] == 0.0
    assert result.overall["missed_item_rate"] == 0.0
    assert result.overall["false_detection_rate"] == 0.0
    assert result.overall["duplicate_detection_rate"] == 1.0
    # Typed by the keyword rules alone: no legend row is left to a person.
    assert suite.untyped[p2_systems.TENDER] == []


def test_the_truth_covers_every_new_type(tmp_path: Path) -> None:
    [tender] = p2_systems.generate(tmp_path).golden_set.tenders

    counted = {entry.object_type for sheet in tender.sheets for entry in sheet.counts}

    assert counted == set(metrics.EQUIPMENT)


def test_the_metric_scores_each_type_on_each_sheet(tmp_path: Path) -> None:
    [truth] = p2_systems.generate(tmp_path).golden_set.tenders
    # One fire pump found of two, on the pump room sheet, and nothing else anywhere.
    prediction = TenderPrediction(
        tender_id=truth.tender_id,
        sheets=(
            SheetPrediction(
                sheet_number="FP-B1-101",
                revision="R01",
                counts=(CountPrediction(object_type=ObjectType.FIRE_PUMP, count=1),),
            ),
        ),
    )

    scored = {m.name: m.value for m in metrics.equipment_count_accuracy(truth, prediction)}

    assert scored["equipment_count_accuracy[FP-B1-101/fire_pump]"] == 0.5
    assert scored["equipment_count_accuracy[FP-B1-101/jockey_pump]"] == 0.0
    assert scored["equipment_count_accuracy[FP-SITE-001/hydrant]"] == 0.0


def test_a_tender_with_no_equipment_has_no_equipment_score(tmp_path: Path) -> None:
    suite = p1_detection.generate(tmp_path, seed=3, tenders=1)

    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite))

    assert result.overall["equipment_count_accuracy"] is None


def test_a_golden_set_is_read_from_its_files(tmp_path: Path) -> None:
    made = p2_systems.generate(tmp_path / "made")
    [truth] = made.golden_set.tenders
    root = tmp_path / "eval"
    (root / "truth" / p2_systems.SUITE).mkdir(parents=True)
    (root / "truth" / p2_systems.SUITE / f"{truth.tender_id}.json").write_text(
        truth.model_dump_json(), encoding="utf-8"
    )
    files = root / "files" / p2_systems.SUITE / truth.tender_id
    files.mkdir(parents=True)
    for path in made.files[truth.tender_id]:
        shutil.copy(path, files / path.name)

    suite = p2_systems.load_golden(root)

    assert suite is not None and not suite.synthetic
    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite))
    assert result.overall["equipment_count_accuracy"] == 1.0


def test_the_command_reports_the_new_types_and_the_gate_includes_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, report = tmp_path / "eval", tmp_path / "p2_systems.md"

    assert main(["--root", str(root), "run", "--suite", "p2_systems", "--report", str(report)]) == 0
    assert "| equipment_count_accuracy | 1.0000 |" in report.read_text(encoding="utf-8")
    assert main(["--root", str(root), "accept", "--suite", "p2_systems", "--approver", "T"]) == 0
    assert main(["--root", str(root), "compare", "--suite", "p2_systems"]) == 0
    assert "No regressions" in capsys.readouterr().out


def test_the_gate_fails_when_equipment_detection_gets_worse(tmp_path: Path) -> None:
    suite = p2_systems.generate(tmp_path)
    good = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite), suite="p2_systems")
    baseline = Baseline.from_result(good, "T")
    worse = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite), suite="p2_systems")
    worse.overall["equipment_count_accuracy"] = 0.9

    assert [r.metric for r in compare(baseline, worse)] == ["equipment_count_accuracy"]


def test_the_committed_baseline_is_met(tmp_path: Path) -> None:
    """The baseline `make eval-gate` compares against, from the repository."""
    committed = Path(__file__).resolve().parents[3] / "eval" / "baselines" / "p2_systems.json"
    suite = p2_systems.generate(tmp_path)

    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite), suite="p2_systems")

    assert compare(Baseline.read(committed), result) == []

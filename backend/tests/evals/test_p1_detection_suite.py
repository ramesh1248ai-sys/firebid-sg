"""The `p1_detection` evaluation suite: the platform's detection scored against truth.

On synthetic installations it must meet the Phase 1 targets (sprinkler count at least 98%,
pipe length within 5%); on a golden set it must read the real files, with only the keyword
rules to type legend rows, and say which rows it could not type.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from firebid.evals import p1_detection
from firebid.evals.cli import main
from firebid.evals.runner import run_suite

pytestmark = pytest.mark.req("FR-VIS-03")


def test_synthetic_installations_meet_the_phase_1_targets(tmp_path: Path) -> None:
    suite = p1_detection.generate(tmp_path, seed=7, tenders=2)

    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite))

    assert result.overall["sprinkler_count_accuracy"] is not None
    assert result.overall["sprinkler_count_accuracy"] >= 0.98
    assert result.overall["pipe_length_error"] is not None
    assert result.overall["pipe_length_error"] <= 0.05
    assert result.overall["false_detection_rate"] == 0.0


def test_a_golden_set_is_read_from_its_files_with_the_rules_alone(tmp_path: Path) -> None:
    """A synthetic tender filed as if it were golden: the upright row, which no rule reads,
    goes untyped and is reported, and its heads are not counted."""
    made = p1_detection.generate(tmp_path / "made", seed=11, tenders=1)
    [truth] = made.golden_set.tenders
    root = tmp_path / "eval"
    (root / "truth" / p1_detection.SUITE).mkdir(parents=True)
    (root / "truth" / p1_detection.SUITE / f"{truth.tender_id}.json").write_text(
        truth.model_dump_json(), encoding="utf-8"
    )
    files = root / "files" / p1_detection.SUITE / truth.tender_id
    files.mkdir(parents=True)
    for path in made.files[truth.tender_id]:
        shutil.copy(path, files / path.name)

    suite = p1_detection.load_golden(root)
    assert suite is not None and not suite.synthetic
    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite))

    assert suite.untyped[truth.tender_id] == ["SPRINKLER - UP TYPE"]
    assert "SPRINKLER - UP TYPE" in p1_detection.untyped_note(suite)
    assert result.overall["sprinkler_count_accuracy"] is not None
    assert result.overall["sprinkler_count_accuracy"] < 1.0, "the uprights are not counted"


def test_no_golden_set_means_none(tmp_path: Path) -> None:
    assert p1_detection.load_golden(tmp_path) is None


def test_the_command_writes_the_report(tmp_path: Path) -> None:
    report = tmp_path / "p1_detection.md"

    code = main(
        [
            "--root",
            str(tmp_path),
            "run",
            "--suite",
            "p1_detection",
            "--tenders",
            "1",
            "--report",
            str(report),
        ]
    )

    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "sprinkler_count_accuracy" in text and "pipe_length_error" in text


@pytest.mark.req("FR-QTO-08")
def test_seeded_duplicates_are_found_and_reported_against_the_target(tmp_path: Path) -> None:
    """An enlarged plan, a riser schematic and a match-lined pair, each repeating a plan."""
    suite = p1_detection.generate(tmp_path, seed=3, tenders=0, with_duplicates=True)

    result = run_suite(suite.golden_set, p1_detection.DetectionPredictor(suite))

    seeded = sum(len(s.duplicates_of) for t in suite.golden_set.tenders for s in t.sheets)
    assert seeded == 3
    assert result.overall["duplicate_detection_rate"] == 1.0
    # Every sheet's own counts and lengths still hold, the match line's carried size included.
    assert result.overall["sprinkler_count_accuracy"] == 1.0
    assert result.overall["pipe_length_error"] is not None
    assert result.overall["pipe_length_error"] <= 0.001

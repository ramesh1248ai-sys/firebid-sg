"""The Phase 1 exit report says what it measured, on what, and what is pending (P1-11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.evals import p1_exit
from firebid.evals.runner import SuiteResult

pytestmark = pytest.mark.req("NFR-14")


def result(**overall: float | None) -> SuiteResult:
    return SuiteResult(
        suite="p1_detection",
        predictor="test",
        predictor_version="1",
        ran_at="2026-09-29T00:00:00Z",
        overall=dict(overall),
    )


def inputs(golden: bool, tmp_path: Path) -> p1_exit.ExitInputs:
    return p1_exit.ExitInputs(
        detection=result(sprinkler_count_accuracy=0.97, pipe_length_error=0.02),
        detection_golden=golden,
        documents=result(drawing_number_accuracy=1.0, revision_accuracy=1.0),
        boq_accuracy=1.0,
        evidence={
            "restore drill": p1_exit.Evidence("restore drill", tmp_path / "missing.json"),
            "load test": p1_exit.Evidence(
                "load test", tmp_path / "load.json", {"summary": "p95 1.2 s at 20 users"}
            ),
        },
    )


def test_without_a_golden_set_the_accuracy_criteria_are_pending(tmp_path: Path) -> None:
    found = inputs(golden=False, tmp_path=tmp_path)

    text = p1_exit.report(found, p1_exit.dynamic_gaps(found))

    assert "synthetic tenders only" in text
    assert text.count("**pending: no golden set**") == 2
    assert "**pending: pilot not run**" in text
    assert "| restore drill | **pending** |" in text
    assert "| load test | recorded | p95 1.2 s at 20 users |" in text
    assert "No golden set: the exit accuracy criteria cannot be measured" in text


def test_on_a_golden_set_a_missed_target_says_so(tmp_path: Path) -> None:
    text = p1_exit.report(inputs(golden=True, tmp_path=tmp_path), [])

    assert "| Sprinkler count on the golden set | ≥98% | 97.0%" in text
    assert "**misses**" in text

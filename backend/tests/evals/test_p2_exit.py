"""The Phase 2 exit report says what it measured, and what is pending (P2-09)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from firebid.evals import p2_exit, p2_workload
from firebid.evals.p1_exit import Evidence
from firebid.evals.runner import Baseline, SuiteResult

pytestmark = pytest.mark.req("NFR-14")

CURATED: dict[str, Any] = {
    "phase2_complete_on": "2026-10-04",
    "security_review": {
        "summary": "Quotations, outcomes and snapshots reviewed.",
        "findings": [
            {"finding": "Free text in the audit log", "severity": "medium", "status": "open"}
        ],
    },
    "gaps": [{"gap": "A known gap", "cause": "its cause", "action": "its action"}],
    "recommendation": "Hold the gate until the pilot has run.",
}


def live(**changes: Any) -> dict[str, Any]:
    return {
        "bids": 3,
        "bids_ready": 3,
        "turnaround_working_days": 13.0,
        "baseline_turnaround_working_days": 20.0,
        "turnaround_reduction": 0.35,
        "priced_lines": 240,
        "sourced_lines": 240,
        "price_provenance": 1.0,
        "clarifications_issued": 12,
        "clarifications_measured": 10,
        "clarifications_minor": 8,
        "clarification_acceptance": 0.8,
        "minor_edit_ratio": 0.2,
        "awarded": 1,
        "lost": 1,
        "agent_runs": 0,
        "cost_sgd": 0.0,
        "target_cost_per_tender_sgd": 50.0,
        "by_model": [],
        "per_bid": [],
        **changes,
    }


def inputs(tmp_path: Path, **changes: Any) -> p2_exit.Phase2Inputs:
    found = p2_exit.Phase2Inputs(
        regressions=[p2_exit.Regression("p1_detection", "the result recorded 2026-09-28", [])],
        evidence={
            "load test": Evidence(
                "load test", tmp_path / "load.json", {"summary": "p95 0.1 s", "meets_nfr02": True}
            )
        },
        recorded={"load test": "2026-10-04"},
        coverage=p2_exit.Coverage(108, 108),
        phase2_since="2026-10-04",
    )
    for name, value in changes.items():
        setattr(found, name, value)
    return found


def test_with_no_pilot_every_criterion_is_pending_and_says_why(tmp_path: Path) -> None:
    text = p2_exit.report(inputs(tmp_path), CURATED)

    assert text.count("**pending: pilot not run**") == 6
    assert "**No pilot data:**" in text
    assert "The assisted-mode pilot has not run." in text
    assert "| No pilot: no Phase 2 exit criterion is measured |" in text
    assert "| A known gap | its cause | its action |" in text
    assert "Hold the gate until the pilot has run." in text
    assert "| Free text in the audit log | medium | open |" in text


def test_a_pilot_that_meets_its_targets_says_so(tmp_path: Path) -> None:
    text = p2_exit.report(inputs(tmp_path, live=live()), CURATED)

    assert (
        "| Estimate turnaround | -30% against the baseline | 13.0 working days over 3 bid(s) "
        "against a baseline of 20 (35.0%) | meets |" in text
    )
    assert "240 of 240 priced lines sourced (100.0%) | meets |" in text
    assert "8 of 10 drafts issued with minor edits (80.0%); 12 issued in all | meets |" in text
    assert "pending" not in text.split("## Pilot findings")[0]


def test_one_unsourced_price_misses_the_criterion(tmp_path: Path) -> None:
    found = live(sourced_lines=239, price_provenance=239 / 240)

    text = p2_exit.report(inputs(tmp_path, live=found), CURATED)

    assert "239 of 240 priced lines sourced (99.6%) | **misses** |" in text


def test_a_turnaround_with_no_baseline_is_pending_not_met(tmp_path: Path) -> None:
    found = live(baseline_turnaround_working_days=None, turnaround_reduction=None)
    gathered = inputs(tmp_path, live=found)

    text = p2_exit.report(gathered, CURATED)

    assert (
        "13.0 working days over 3 bid(s); no baseline recorded | **pending: no baseline** |" in text
    )
    assert p2_exit.dynamic_gaps(gathered)[0][0].startswith("No turnaround baseline")


def test_a_regression_is_reported_and_becomes_a_gap(tmp_path: Path) -> None:
    worse = p2_exit.Regression(
        "p1_detection", "the result recorded 2026-09-28", ["pipe_length_error: 0.0000 -> 0.0900"]
    )
    gathered = inputs(tmp_path, regressions=[worse])

    text = p2_exit.report(gathered, CURATED)

    assert (
        "| p1_detection | the result recorded 2026-09-28 | **regressed**: pipe_length_error" in text
    )
    assert ("Regression in p1_detection") in [gap for gap, _, _ in p2_exit.dynamic_gaps(gathered)]


@pytest.mark.req("NFR-01", "NFR-02")
def test_evidence_recorded_before_phase_2_is_not_counted_as_rerun(tmp_path: Path) -> None:
    gathered = inputs(tmp_path, recorded={"load test": "2026-09-29"})

    text = p2_exit.report(gathered, CURATED)

    assert "| load test | 2026-09-29 | **no** | recorded | p95 0.1 s |" in text
    assert "| Load test: not rerun with Phase 2 in place | last recorded 2026-09-29 |" in text
    assert "| load test | 2026-10-04 | yes |" in p2_exit.report(inputs(tmp_path), CURATED)


def test_a_requirement_with_no_test_is_named(tmp_path: Path) -> None:
    table = tmp_path / "coverage.md"
    table.write_text(
        "Requirement coverage: 2/3 covered\n\n"
        "| ID | Priority | Phase | Tests |\n|---|---|---|---|\n"
        "| FR-DSN-04 | M | P1 | backend/tests/qto/test_designed.py::test_a |\n"
        "| FR-DSN-05 | S | P2 | - |\n"
        "| NFR-01 | - | P1 | backend/tests/x.py::test_b |\n",
        encoding="utf-8",
    )

    found = p2_exit.read_coverage(table)

    assert found is not None
    assert (found.covered, found.total, found.uncovered) == (2, 3, [("FR-DSN-05", "S", "P2")])
    text = p2_exit.report(inputs(tmp_path, coverage=found), CURATED)
    assert "**2 of 3** requirements" in text
    assert "| FR-DSN-05 | S | P2 | **no test**: see the gap list |" in text
    assert p2_exit.read_coverage(tmp_path / "missing.md") is None


def test_a_suite_is_compared_with_what_it_measured_before() -> None:
    def result(error: float) -> SuiteResult:
        return SuiteResult(
            suite="p1_detection",
            predictor="test",
            predictor_version="1",
            ran_at="2026-10-04T00:00:00Z",
            overall={"sprinkler_count_accuracy": 1.0, "pipe_length_error": error},
        )

    before = Baseline("p1_detection", "", "2026-09-28", "test", "1", dict(result(0.0).overall))

    same = p2_exit._against("p1_detection", result(0.0), (before, "2026-09-28"))
    worse = p2_exit._against("p1_detection", result(0.2), (before, "2026-09-28"))
    alone = p2_exit._against("p1_detection", result(0.0), None)

    assert (same.status, same.against) == ("no regression", "the result recorded 2026-09-28")
    assert worse.status == "**regressed**" and "pipe_length_error" in (worse.worse or [""])[0]
    assert alone.status == "**pending: nothing to compare with**"


@pytest.mark.req("NFR-01")
def test_the_estimating_work_of_phase_2_stays_inside_the_interaction_budget() -> None:
    timings = p2_workload.run()

    assert [timing.workload for timing in timings] == [name for name, _, _ in p2_workload.WORKLOADS]
    assert all(timing.within_budget for timing in timings), timings

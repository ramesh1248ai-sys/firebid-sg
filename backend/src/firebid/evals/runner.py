"""Running a suite, reporting it, and refusing a regression.

Three things live here:

* **The runner** scores a predictor against a golden set and writes a report sliced by input
  class and consultant, because an average across both hides exactly the failure that matters
  — a good score on vector drawings carrying a bad one on scans.
* **The baseline**, which is what "no worse than before" is measured against. Accepting one is
  a deliberate act with a named approver, not something that happens on every run.
* **The gate**, which fails when a metric regresses past its tolerance (FR-LRN-01).

A metric that has become *undefined* since the baseline is treated as a regression. Measuring
nothing is not an improvement, and silently dropping it is how a suite stops testing something
without anyone noticing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from firebid.evals.metrics import (
    HIGHER_IS_BETTER,
    PHASE_1_TARGETS,
    Aggregate,
    TenderScore,
    aggregate,
    score_tender,
)
from firebid.evals.prediction import Predictor
from firebid.evals.schema import GoldenSet

# How much a metric may move before it counts as a regression. Deliberately not zero: these
# are means over a small set, and a gate that fires on noise gets switched off.
DEFAULT_TOLERANCE = 0.01
TOLERANCES: dict[str, float] = {
    "sprinkler_count_accuracy": 0.01,
    "pipe_length_error": 0.01,
    "missed_item_rate": 0.01,
    "false_detection_rate": 0.01,
    "duplicate_detection_rate": 0.02,
    "sheet_classification_accuracy": 0.01,
    "boq_mapping_accuracy": 0.02,
    "calibration_error": 0.05,
}


@dataclass
class SuiteResult:
    """One run of one suite."""

    suite: str
    predictor: str
    predictor_version: str
    ran_at: str
    tenders: list[TenderScore] = field(default_factory=list)
    overall: dict[str, float | None] = field(default_factory=dict)
    by_input_class: dict[str, dict[str, float | None]] = field(default_factory=dict)
    by_consultant: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # Set by compare-models; None for an ordinary run.
    route: str | None = None
    model: str | None = None
    config_version: str | None = None
    prompt_versions: list[str] = field(default_factory=list)
    cost_sgd: str | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None

    def to_json(self) -> str:
        payload = asdict(self)
        # TenderScore holds dataclasses; asdict has already flattened them.
        return json.dumps(payload, indent=2, default=str)


def _mean_over(scores: Sequence[TenderScore], metric: str) -> float | None:
    values = [
        score.named_values()[metric]
        for score in scores
        if score.named_values().get(metric) is not None
    ]
    return sum(v for v in values if v is not None) / len(values) if values else None


def _summarise(scores: Sequence[TenderScore]) -> dict[str, float | None]:
    return {metric: _mean_over(scores, metric) for metric in HIGHER_IS_BETTER}


def run_suite(
    golden_set: GoldenSet, predictor: Predictor, *, suite: str | None = None
) -> SuiteResult:
    """Score a predictor over a whole set."""
    scores = [score_tender(tender, predictor.predict(tender)) for tender in golden_set.tenders]

    by_class: dict[str, list[TenderScore]] = {}
    by_consultant: dict[str, list[TenderScore]] = {}
    for score in scores:
        by_class.setdefault(score.input_class, []).append(score)
        by_consultant.setdefault(score.consultant, []).append(score)

    return SuiteResult(
        suite=suite or golden_set.name,
        predictor=predictor.name,
        predictor_version=predictor.version,
        ran_at=datetime.now(UTC).isoformat(),
        tenders=scores,
        overall=_summarise(scores),
        by_input_class={key: _summarise(value) for key, value in sorted(by_class.items())},
        by_consultant={key: _summarise(value) for key, value in sorted(by_consultant.items())},
    )


# ---------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------


def _format(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _target_note(metric: str, value: float | None) -> str:
    """Whether a metric meets its §14 target, where it has one."""
    target = PHASE_1_TARGETS.get(metric)
    if target is None or value is None:
        return ""
    met = value >= target if HIGHER_IS_BETTER[metric] else value <= target
    direction = "≥" if HIGHER_IS_BETTER[metric] else "≤"
    return f"{'meets' if met else 'MISSES'} {direction}{target}"


def markdown_report(result: SuiteResult) -> str:
    lines = [
        f"# Evaluation — {result.suite}",
        "",
        f"- Predictor: `{result.predictor}` {result.predictor_version}",
        f"- Run at: {result.ran_at}",
        f"- Tenders: {len(result.tenders)}",
    ]
    if result.model:
        lines.append(f"- Route `{result.route}` on model `{result.model}`")
    if result.config_version:
        lines.append(f"- Configuration version: `{result.config_version}`")
    if result.prompt_versions:
        lines.append(f"- Prompt versions: {', '.join(sorted(set(result.prompt_versions)))}")
    lines += ["", "## Overall", "", "| Metric | Value | Target |", "|---|---|---|"]
    for metric, value in result.overall.items():
        lines.append(f"| {metric} | {_format(value)} | {_target_note(metric, value)} |")

    for title, grouped in (
        ("By input class", result.by_input_class),
        ("By consultant", result.by_consultant),
    ):
        if not grouped:
            continue
        lines += ["", f"## {title}", ""]
        metrics = list(HIGHER_IS_BETTER)
        lines.append("| Group | " + " | ".join(metrics) + " |")
        lines.append("|---" * (len(metrics) + 1) + "|")
        for group, values in grouped.items():
            row = " | ".join(_format(values.get(metric)) for metric in metrics)
            lines.append(f"| {group} | {row} |")

    excluded = [
        f"{score.tender_id}: {name} ({', '.join(sorted(reasons))})"
        for score in result.tenders
        for name, reasons in (
            ("sprinkler_count_accuracy", score.sprinkler_count_accuracy.excluded_reasons),
            ("pipe_length_error", score.pipe_length_error.excluded_reasons),
        )
        if reasons
    ]
    if excluded:
        lines += ["", "## Not measured", ""]
        lines += [f"- {line}" for line in excluded]

    lines += [
        "",
        "> Synthetic fixtures prove the pipeline, not real-world accuracy. Only the golden "
        "set (decision D3) can do that.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# Baselines and the gate
# ---------------------------------------------------------------------------------------


@dataclass
class Baseline:
    suite: str
    approver: str
    accepted_at: str
    predictor: str
    predictor_version: str
    metrics: dict[str, float | None]

    @staticmethod
    def from_result(result: SuiteResult, approver: str) -> Baseline:
        return Baseline(
            suite=result.suite,
            approver=approver,
            accepted_at=datetime.now(UTC).isoformat(),
            predictor=result.predictor,
            predictor_version=result.predictor_version,
            metrics=dict(result.overall),
        )

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @staticmethod
    def read(path: Path) -> Baseline:
        return Baseline(**json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Regression:
    metric: str
    baseline: float | None
    current: float | None
    tolerance: float

    def __str__(self) -> str:
        if self.baseline is None:
            return f"{self.metric}: no baseline value"
        if self.current is None:
            return f"{self.metric}: was {self.baseline:.4f}, now not measured"
        moved = abs(self.current - self.baseline)
        return (
            f"{self.metric}: {self.baseline:.4f} -> {self.current:.4f} "
            f"(moved {moved:.4f}, tolerance {self.tolerance:.4f})"
        )


def compare(baseline: Baseline, result: SuiteResult) -> list[Regression]:
    """What has got worse by more than its tolerance. Empty means the gate passes."""
    regressions: list[Regression] = []
    for metric, previous in baseline.metrics.items():
        current = result.overall.get(metric)
        tolerance = TOLERANCES.get(metric, DEFAULT_TOLERANCE)

        if previous is None:
            continue  # nothing to regress from
        if current is None:
            # Measuring nothing is not an improvement.
            regressions.append(Regression(metric, previous, None, tolerance))
            continue

        higher_is_better = HIGHER_IS_BETTER.get(metric, True)
        worsened = (previous - current) if higher_is_better else (current - previous)
        if worsened > tolerance:
            regressions.append(Regression(metric, previous, current, tolerance))
    return regressions


def regression_report(regressions: Sequence[Regression]) -> str:
    if not regressions:
        return "No regressions.\n"
    lines = [f"{len(regressions)} regression(s):", ""]
    lines += [f"  - {regression}" for regression in regressions]
    lines += ["", "Fix them, or accept a new baseline with: firebid-eval accept --approver <name>"]
    return "\n".join(lines) + "\n"


def aggregate_named(scores: Sequence[TenderScore], metric: str) -> Aggregate:
    """A named aggregate over tenders, for callers that want the exclusion counts too."""
    from firebid.evals.metrics import MetricValue

    return aggregate(
        metric,
        [
            MetricValue(name=metric, value=score.named_values().get(metric))
            if score.named_values().get(metric) is not None
            else MetricValue(name=metric, value=None, undefined_reason="not measurable")
            for score in scores
        ],
    )

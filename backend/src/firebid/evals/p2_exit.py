"""The Phase 2 exit report (P2-09): every KPI and exit criterion against target, and the gaps.

    firebid-eval exit-p2 [--live] [--out ../docs/reports/phase2-exit.md]

It gathers what exists and says plainly what does not:

* the Phase 2 KPIs (requirements §14) from live bids (`--live`; `services.kpis.phase2_kpis`):
  tender turnaround, price provenance, clarification acceptance. With no pilot they are
  pending, never assumed;
* regression: the Phase 1 detection, document and mapping suites measured again, detection
  against the result recorded before Phase 2, and the suites with an accepted baseline
  against it (the gate);
* the non-functional evidence under `eval/results/`, each with the day it was recorded, and
  the Phase 2 workload benchmark (`evals.p2_workload`);
* requirement coverage for Phase 2 (`make req-coverage PHASE=P2`);
* what people wrote down: pilot findings, the reviews, the known gaps and the recommendation
  (`docs/reports/phase2-gaps.yaml`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firebid.evals import p1_exit
from firebid.evals.p1_exit import Evidence
from firebid.evals.runner import Baseline, SuiteResult, compare

TARGETS = {
    "turnaround_reduction": 0.30,
    "price_provenance": 1.0,
    "clarification_acceptance": 0.70,
}
# The benchmarks that need the running stack or an environment: rerun for Phase 2, or not.
MISSED_ACTIONS = {
    **p1_exit.MISSED_ACTIONS,
    "Phase 2 workload benchmark": "profile the workload over its budget "
    "(`python -m firebid.evals.p2_workload`); fix; re-run",
}
ID = re.compile(r"^\| ((?:FR-[A-Z]+-\d{2}|NFR-\d{2})) \| ([^|]*) \| (P\d) \| (.*) \|$")


@dataclass
class Regression:
    """One suite measured again, and what it was compared with."""

    suite: str
    against: str
    # None: nothing to compare with. Empty: nothing got worse.
    worse: list[str] | None
    measured: dict[str, float | None] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.worse is None:
            return "**pending: nothing to compare with**"
        return "no regression" if not self.worse else "**regressed**"


@dataclass
class Coverage:
    covered: int
    total: int
    # (id, priority, phase) of each requirement with no test.
    uncovered: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class Phase2Inputs:
    live: dict[str, Any] | None = None
    regressions: list[Regression] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    recorded: dict[str, str] = field(default_factory=dict)
    coverage: Coverage | None = None
    # Since when the non-functional evidence counts as rerun with Phase 2 in place.
    phase2_since: str = ""


def read_coverage(path: Path) -> Coverage | None:
    """The table `scripts/req_coverage.py --output` writes."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    rows = [match.groups() for line in lines if (match := ID.match(line))]
    if not rows:
        return None
    uncovered = [(i, priority.strip(), phase) for i, priority, phase, tests in rows if tests == "-"]
    return Coverage(len(rows) - len(uncovered), len(rows), uncovered)


def _prior(path: Path) -> tuple[Baseline, str] | None:
    """An earlier result of a suite, as something to compare with."""
    data = p1_exit._load(path)
    if not data or "overall" not in data:
        return None
    ran = str(data.get("ran_at", ""))[:10]
    return (
        Baseline(
            suite=str(data.get("suite", "")),
            approver="",
            accepted_at=ran,
            predictor=str(data.get("predictor", "")),
            predictor_version=str(data.get("predictor_version", "")),
            metrics=dict(data["overall"]),
        ),
        ran,
    )


def _recorded(suite: str, figures: dict[str, Any] | None) -> tuple[Baseline, str] | None:
    """A suite's figures as an earlier report recorded them, where no result file was kept."""
    if not figures:
        return None
    when = str(figures.get("recorded", ""))
    metrics = {key: float(value) for key, value in figures.items() if key != "recorded"}
    return Baseline(suite, "", when, "", "", dict(metrics)), when


def _against(
    suite: str,
    result: SuiteResult,
    prior: tuple[Baseline, str] | None,
    metrics: tuple[str, ...] | None = None,
) -> Regression:
    measured: dict[str, float | None] = {
        key: value
        for key, value in result.overall.items()
        if value is not None and (metrics is None or key in metrics)
    }
    if prior is None:
        return Regression(suite, "-", None, measured)
    baseline, when = prior
    label = (
        f"the baseline accepted {when} by {baseline.approver}"
        if baseline.approver
        else f"the result recorded {when}"
    )
    return Regression(suite, label, [str(item) for item in compare(baseline, result)], measured)


def regressions(
    root: Path, seed: int = 1, recorded: dict[str, Any] | None = None
) -> list[Regression]:
    """The Phase 1 suites and the Phase 2 systems suite, measured now with everything
    Phase 2 added in place, each against what it measured before: its last result file,
    its accepted baseline, or the figures an earlier report recorded (`recorded`)."""
    from firebid.evals import doc_classification, p1_boq, p1_detection, p2_systems
    from firebid.evals.runner import run_suite

    results = root / "results"
    out: list[Regression] = []

    detection_suite = p1_detection.load_golden(root) or p1_detection.generate(
        root / "synthetic" / p1_detection.SUITE, seed=seed, tenders=3, with_duplicates=True
    )
    detection = run_suite(
        detection_suite.golden_set, p1_detection.DetectionPredictor(detection_suite)
    )
    out.append(_against("p1_detection", detection, _prior(results / "p1_detection.json")))

    documents_suite = doc_classification.load_golden(root) or doc_classification.generate(
        root / "synthetic" / "doc_classification", seed=seed
    )
    documents = run_suite(
        documents_suite.golden_set, doc_classification.TitleBlockPredictor(documents_suite.files)
    )
    out.append(
        _against(
            "doc_classification",
            documents,
            _prior(results / "doc_classification.json")
            or _recorded("doc_classification", (recorded or {}).get("doc_classification")),
            metrics=(
                "drawing_number_accuracy",
                "revision_accuracy",
                "sheet_classification_accuracy",
            ),
        )
    )

    systems_suite = p2_systems.load_golden(root) or p2_systems.generate(
        root / "synthetic" / "p2_systems"
    )
    systems = run_suite(
        systems_suite.golden_set,
        p1_detection.DetectionPredictor(systems_suite),
        suite="p2_systems",
    )
    baseline_path = root / "baselines" / "p2_systems.json"
    accepted = Baseline.read(baseline_path) if baseline_path.exists() else None
    out.append(
        _against("p2_systems", systems, (accepted, accepted.accepted_at[:10]) if accepted else None)
    )

    mapping = p1_boq.evaluate()
    out.append(
        Regression(
            "p1_boq",
            "its target of 90%",
            [] if mapping.met else [f"mapping accuracy {mapping.accuracy:.3f} misses its target"],
            {"boq_mapping_accuracy": mapping.accuracy},
        )
    )
    return out


def gather(
    root: Path,
    live: bool,
    coverage: Path | None = None,
    phase2_since: str = "",
    recorded: dict[str, Any] | None = None,
) -> Phase2Inputs:
    inputs = Phase2Inputs(
        regressions=regressions(root, recorded=recorded), phase2_since=phase2_since
    )
    results = root / "results"
    for name, file in (
        ("Phase 2 workload benchmark", "bench/p2_workload.json"),
        ("load test", "bench/load.json"),
        ("ingest benchmark", "bench/ingest.json"),
        ("QTO benchmark", "bench/qto.json"),
        ("restore drill", "ops/restore-drill.json"),
        ("provider game day", "ops/game-day.json"),
        ("deployment guard", "ops/deploy-guard.json"),
        ("provider data terms", "ops/provider-terms.json"),
    ):
        path = results / file
        inputs.evidence[name] = Evidence(name, path, p1_exit._load(path))
        if path.exists():
            inputs.recorded[name] = f"{datetime.fromtimestamp(path.stat().st_mtime, UTC):%Y-%m-%d}"
    inputs.coverage = read_coverage(coverage or results / "req-coverage-p2.md")
    if live:
        inputs.live = live_kpis()
    return inputs


def live_kpis() -> dict[str, Any]:
    """Every bid's Phase 2 measures, read on the service role (a cross-bid report)."""
    from sqlalchemy import select

    from firebid.db.engine import service_session_scope
    from firebid.db.models.core import Bid
    from firebid.db.models.submission import BidOutcome
    from firebid.services import kpis

    with service_session_scope() as session:
        bids = list(session.execute(select(Bid).order_by(Bid.human_id)).scalars())
        found = kpis.phase2_kpis(session, bids)
        stats = kpis.agent_stats(session)
        outcomes = [row.outcome for row in session.execute(select(BidOutcome)).scalars()]
    return {
        "bids": len(found.bids),
        "bids_ready": sum(1 for row in found.bids if row.turnaround_working_days is not None),
        "turnaround_working_days": found.turnaround_working_days,
        "baseline_turnaround_working_days": found.baseline_turnaround_working_days,
        "turnaround_reduction": found.turnaround_reduction,
        "priced_lines": sum(row.priced_lines for row in found.bids),
        "sourced_lines": sum(row.sourced_lines for row in found.bids),
        "price_provenance": found.price_provenance,
        "clarifications_issued": sum(row.clarifications_issued for row in found.bids),
        "clarifications_measured": sum(row.clarifications_measured for row in found.bids),
        "clarifications_minor": sum(row.clarifications_minor for row in found.bids),
        "clarification_acceptance": found.clarification_acceptance,
        "minor_edit_ratio": found.minor_edit_ratio,
        "awarded": outcomes.count("awarded"),
        "lost": outcomes.count("lost"),
        "agent_runs": stats.runs,
        "cost_sgd": float(stats.cost_sgd),
        "target_cost_per_tender_sgd": float(kpis.target_cost_per_tender() or 0) or None,
        "by_model": [{**row, "cost_sgd": float(row["cost_sgd"])} for row in stats.by_model[:10]],
        "per_bid": [
            {
                "human_id": row.human_id,
                "turnaround_working_days": row.turnaround_working_days,
                "price_provenance": row.price_provenance,
                "priced_lines": row.priced_lines,
                "clarification_acceptance": row.clarification_acceptance,
                "clarifications_measured": row.clarifications_measured,
            }
            for row in found.bids
        ],
    }


# --- The report -----------------------------------------------------------------------------


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _status(value: float | None, target: float, pending: str) -> str:
    if value is None:
        return f"**pending: {pending}**"
    # A share that is all but the target is not the target: 99.96% of prices is not "every".
    return "meets" if value >= target else "**misses**"


def _turnaround(live: dict[str, Any]) -> tuple[str, str]:
    """The turnaround criterion's evidence and status, from live bids."""
    days = live.get("turnaround_working_days")
    baseline = live.get("baseline_turnaround_working_days")
    if days is None:
        return "no bid has reached G3", "**pending: no bid has reached G3**"
    evidence = f"{days:.1f} working days over {live['bids_ready']} bid(s)"
    if baseline is None:
        return f"{evidence}; no baseline recorded", "**pending: no baseline**"
    evidence += f" against a baseline of {baseline:g} ({_fmt(live.get('turnaround_reduction'))})"
    return evidence, _status(live.get("turnaround_reduction"), TARGETS["turnaround_reduction"], "")


def report(inputs: Phase2Inputs, curated: dict[str, Any]) -> str:
    live = inputs.live
    lines = [
        "# Phase 2 exit report",
        "",
        f"Generated {datetime.now(UTC):%d %b %Y %H:%M} UTC by `firebid-eval exit-p2`. "
        + (
            f"KPIs measured on **{live['bids']} live bid(s)**."
            if live
            else "**No pilot data:** the KPIs below are instrumented and pending measurement."
        ),
        "",
        "## Exit criteria (requirements §13.2)",
        "",
        "| Criterion | Target | Evidence | Status |",
        "|---|---|---|---|",
    ]
    if live:
        turnaround, turnaround_status = _turnaround(live)
        provenance = (
            f"{live['sourced_lines']} of {live['priced_lines']} priced lines sourced "
            f"({_fmt(live.get('price_provenance'))})"
        )
        acceptance = (
            f"{live['clarifications_minor']} of {live['clarifications_measured']} drafts issued "
            f"with minor edits ({_fmt(live.get('clarification_acceptance'))}); "
            f"{live['clarifications_issued']} issued in all"
        )
        provenance_status = _status(
            live.get("price_provenance"), TARGETS["price_provenance"], "no priced bill"
        )
        acceptance_status = _status(
            live.get("clarification_acceptance"),
            TARGETS["clarification_acceptance"],
            "no clarification issued",
        )
    else:
        turnaround = provenance = acceptance = "not measured"
        turnaround_status = provenance_status = acceptance_status = "**pending: pilot not run**"
    lines += [
        f"| Estimate turnaround | -30% against the baseline | {turnaround} | {turnaround_status} |",
        f"| Zero unsourced prices | 100% of priced lines sourced | {provenance} | "
        f"{provenance_status} |",
        f"| Clarifications issued with only minor edits | ≥70% | {acceptance} | "
        f"{acceptance_status} |",
        "",
        "## Phase 2 KPIs (requirements §14)",
        "",
        "| KPI | How it is measured | Target | Measured | Status |",
        "|---|---|---|---|---|",
        "| Tender turnaround | working days from the day the bid was opened to its first G3 "
        "approval (weekends and configured holidays left out), averaged over bids, against "
        f"`baseline_turnaround_working_days` | -30% | {turnaround} | {turnaround_status} |",
        "| Price provenance | priced lines of the current bill with a rate from the library, a "
        f"quotation or a named allowance, over priced lines | 100% | {provenance} | "
        f"{provenance_status} |",
        "| Clarification acceptance | drafts issued with a word-level edit distance from the "
        "draft at or under "
        f"{_fmt((live or {}).get('minor_edit_ratio', 0.2))} of the longer text, over drafts "
        f"issued | ≥70% | {acceptance} | {acceptance_status} |",
        "| Business outcomes | tenders awarded and lost, from recorded outcomes | monitor | "
        + (f"{live['awarded']} awarded, {live['lost']} lost" if live else "not measured")
        + " | monitored |",
    ]
    if live and live.get("per_bid"):
        lines += [
            "",
            "### By bid",
            "",
            "| Bid | Turnaround (working days) | Priced lines | Price provenance | "
            "Clarifications measured | Acceptance |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {row['human_id']} | "
            f"{'-' if row['turnaround_working_days'] is None else row['turnaround_working_days']}"
            f" | {row['priced_lines']} | {_fmt(row['price_provenance'])} | "
            f"{row['clarifications_measured']} | {_fmt(row['clarification_acceptance'])} |"
            for row in live["per_bid"]
        ]

    lines += ["", "## Pilot findings", ""]
    findings = curated.get("pilot_findings") or []
    lines += [f"- {_flat(item)}" for item in findings] or ["The assisted-mode pilot has not run."]

    lines += [
        "",
        "## Regression (Phase 1 suites, with Phase 2 in place)",
        "",
        "| Suite | Compared with | Result | Measured now |",
        "|---|---|---|---|",
    ]
    for item in inputs.regressions:
        measured = ", ".join(
            f"{name} {value:.4g}" for name, value in item.measured.items() if value is not None
        )
        result = item.status + (": " + "; ".join(item.worse) if item.worse else "")
        lines.append(f"| {item.suite} | {item.against} | {result} | {measured or '-'} |")

    lines += [
        "",
        "## Non-functional evidence",
        "",
        "| Evidence | Recorded | With Phase 2 in place | Status | Result |",
        "|---|---|---|---|---|",
    ]
    for evidence in inputs.evidence.values():
        if not evidence.present or evidence.data is None:
            lines.append(f"| {evidence.name} | - | - | **pending** | not yet run |")
            continue
        recorded = inputs.recorded.get(evidence.name, "-")
        current = "yes" if inputs.phase2_since and recorded >= inputs.phase2_since else "**no**"
        verdict = "**misses**" if evidence.passed is False else "recorded"
        lines.append(
            f"| {evidence.name} | {recorded} | {current} | {verdict} | {_summary(evidence)} |"
        )

    lines += ["", "## Requirement coverage", ""]
    if inputs.coverage is None:
        lines.append("**Pending:** run `make req-coverage PHASE=P2`.")
    else:
        found = inputs.coverage
        lines.append(
            f"`make req-coverage PHASE=P2`: **{found.covered} of {found.total}** requirements up "
            "to Phase 2 have at least one test."
        )
        if found.uncovered:
            lines += ["", "| Requirement | Priority | Phase | Status |", "|---|---|---|---|"]
            lines += [
                f"| {req} | {priority} | {phase} | **no test**: see the gap list |"
                for req, priority, phase in found.uncovered
            ]

    for title, key in (
        ("Security and privacy review", "security_review"),
        ("AI cost and provider review", "cost_review"),
    ):
        lines += ["", f"## {title}", ""]
        review = curated.get(key) or {}
        if not review:
            lines.append("**Pending:** not yet reviewed.")
            continue
        lines.append(_flat(review.get("summary", "")))
        if key == "cost_review" and live:
            lines += [
                "",
                f"{live['agent_runs']} agent runs across {live['bids']} bids cost SGD "
                f"{live['cost_sgd']:.2f} against a target of SGD "
                + (
                    f"{live['target_cost_per_tender_sgd']:.2f}"
                    if live.get("target_cost_per_tender_sgd")
                    else "-"
                )
                + " per tender (a placeholder).",
            ]
            if live.get("by_model"):
                lines += [
                    "",
                    "| Route | Provider | Model | Runs | Cost SGD |",
                    "|---|---|---|---|---|",
                ]
                lines += [
                    f"| {row['route']} | {row['provider']} | {row['model']} | {row['runs']} | "
                    f"{row['cost_sgd']:.2f} |"
                    for row in live["by_model"]
                ]
        rows = review.get("findings") or []
        if rows:
            lines += ["", "| Finding | Severity | Status |", "|---|---|---|"]
            lines += [
                f"| {_flat(row['finding'])} | {row['severity']} | {_flat(row['status'])} |"
                for row in rows
            ]

    gaps = dynamic_gaps(inputs) + [
        (g["gap"], _flat(g["cause"]), _flat(g["action"])) for g in curated.get("gaps") or []
    ]
    lines += ["", "## Gap list", "", "| Gap | Cause | Proposed action |", "|---|---|---|"]
    lines += [f"| {gap} | {cause} | {action} |" for gap, cause, action in gaps]

    lines += ["", "## Recommendation", ""]
    lines.append(_flat(curated.get("recommendation") or "**Pending:** none recorded."))
    return "\n".join(lines) + "\n"


def _flat(value: object) -> str:
    return " ".join(str(value).split())


def _summary(evidence: Evidence) -> str:
    data = evidence.data or {}
    if "summary" in data:
        return str(data["summary"])
    timings = data.get("timings")
    if isinstance(timings, list) and timings:
        slowest = max(timings, key=lambda item: item["seconds"])
        return (
            f"{len(timings)} workloads; slowest {slowest['workload']} at "
            f"{slowest['seconds']} s on {slowest['size']}, against {data.get('budget_seconds')} s"
        )
    return "recorded"


def dynamic_gaps(inputs: Phase2Inputs) -> list[tuple[str, str, str]]:
    """Gaps the numbers themselves show."""
    gaps: list[tuple[str, str, str]] = []
    if inputs.live is None:
        gaps.append(
            (
                "No pilot: no Phase 2 exit criterion is measured",
                "the assisted-mode pilot on live tenders has not run (business track)",
                "run the pilot; then `make exit-report-p2 LIVE=1`",
            )
        )
    elif inputs.live.get("baseline_turnaround_working_days") is None:
        gaps.append(
            (
                "No turnaround baseline: the turnaround criterion cannot be judged",
                "the baseline working days per tender have not been recorded (business track)",
                "set `phase2.baseline_turnaround_working_days` in `config/kpi.yaml`; re-run",
            )
        )
    for item in inputs.regressions:
        if item.worse:
            gaps.append(
                (
                    f"Regression in {item.suite}",
                    "; ".join(item.worse),
                    "fix it, or accept a new baseline with the product owner's approval",
                )
            )
    for name, evidence in inputs.evidence.items():
        if not evidence.present:
            gaps.append((f"{name.capitalize()}: no evidence yet", "not run", "run it"))
        elif evidence.passed is False and evidence.data is not None:
            gaps.append(
                (
                    f"{name.capitalize()}: misses its target",
                    str(evidence.data.get("summary", evidence.path.name)),
                    MISSED_ACTIONS.get(name, "investigate; re-run"),
                )
            )
        elif not (inputs.phase2_since and inputs.recorded.get(name, "") >= inputs.phase2_since):
            gaps.append(
                (
                    f"{name.capitalize()}: not rerun with Phase 2 in place",
                    f"last recorded {inputs.recorded.get(name, '-')}",
                    "re-run it on the current build (see runbooks)",
                )
            )
    if inputs.coverage is None:
        gaps.append(
            ("Requirement coverage not reported", "not run", "`make req-coverage PHASE=P2`")
        )
    return gaps

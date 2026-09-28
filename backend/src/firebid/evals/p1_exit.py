"""The Phase 1 exit report (P1-11): every KPI and exit criterion against target, and the gaps.

    firebid-eval exit [--indicative-root DIR] [--live] [--out ../docs/reports/phase1-exit.md]

It gathers what exists and says plainly what does not:

* the detection suite (§14 KPIs sliced by input class and consultant), the document suite
  and the BOQ mapping suite, on the golden set when there is one and synthetic tenders when
  not;
* an **indicative** sample (`--indicative-root`), real drawings whose truth is not an
  estimator's verified takeoff, reported apart and never as the golden set;
* live bids (`--live`): what they measure on their own (`services.kpis`);
* evidence files other parts of P1-11 write under `eval/results/`: shadow reports,
  benchmarks, the load test, the restore drill and the provider game day. A missing one is
  reported as pending, never assumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firebid.evals.runner import SuiteResult, run_suite

TARGETS = {
    "sprinkler_count_accuracy": (">=", 0.98),
    "pipe_length_error": ("<=", 0.05),
    "missed_item_rate": ("<=", 0.05),
    "false_detection_rate": ("<=", 0.05),
    "duplicate_detection_rate": (">=", 0.95),
}
LABELS = {
    "sprinkler_count_accuracy": "Sprinkler count accuracy",
    "pipe_length_error": "Pipe length error",
    "missed_item_rate": "Missed-item rate",
    "false_detection_rate": "False-detection rate",
    "duplicate_detection_rate": "Duplicate detection",
}


def _meets(metric: str, value: float | None) -> str:
    if value is None:
        return "not measured"
    sign, target = TARGETS[metric]
    ok = value >= target if sign == ">=" else value <= target
    return "meets" if ok else "**misses**"


def _fmt(value: float | None, percent: bool = True) -> str:
    if value is None:
        return "-"
    return f"{100 * value:.1f}%" if percent else f"{value:g}"


@dataclass
class Evidence:
    name: str
    path: Path
    data: dict[str, Any] | None = None

    @property
    def present(self) -> bool:
        return self.data is not None


@dataclass
class ExitInputs:
    detection: SuiteResult
    detection_golden: bool
    documents: SuiteResult
    boq_accuracy: float | None
    indicative: SuiteResult | None = None
    indicative_note: str = ""
    live: dict[str, Any] | None = None
    shadow: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def gather(root: Path, indicative_root: Path | None, live: bool, seed: int = 1) -> ExitInputs:
    from firebid.evals import doc_classification, p1_boq, p1_detection

    detection_suite = p1_detection.load_golden(root) or p1_detection.generate(
        root / "synthetic" / p1_detection.SUITE, seed=seed, tenders=3, with_duplicates=True
    )
    detection = run_suite(
        detection_suite.golden_set, p1_detection.DetectionPredictor(detection_suite)
    )
    documents_suite = doc_classification.load_golden(root) or doc_classification.generate(
        root / "synthetic" / "doc_classification", seed=seed
    )
    documents = run_suite(
        documents_suite.golden_set, doc_classification.TitleBlockPredictor(documents_suite.files)
    )
    inputs = ExitInputs(
        detection=detection,
        detection_golden=not detection_suite.synthetic,
        documents=documents,
        boq_accuracy=p1_boq.evaluate().accuracy,
    )
    if indicative_root is not None:
        sample = p1_detection.load_golden(indicative_root)
        if sample is not None:
            inputs.indicative = run_suite(
                sample.golden_set, p1_detection.DetectionPredictor(sample)
            )
            inputs.indicative_note = " ".join(t.notes for t in sample.golden_set.tenders)
    if live:
        inputs.live = live_kpis()
    results = root / "results"
    inputs.shadow = [
        data for path in sorted((results / "shadow").glob("*.json")) if (data := _load(path))
    ]
    for name, file in (
        ("ingest benchmark", "bench/ingest.json"),
        ("QTO benchmark", "bench/qto.json"),
        ("load test", "bench/load.json"),
        ("restore drill", "ops/restore-drill.json"),
        ("provider game day", "ops/game-day.json"),
        ("deployment guard", "ops/deploy-guard.json"),
        ("provider data terms", "ops/provider-terms.json"),
    ):
        inputs.evidence[name] = Evidence(name, results / file, _load(results / file))
    return inputs


def live_kpis() -> dict[str, Any]:
    """Every bid's own measures, read on the service role (a cross-bid report)."""
    from sqlalchemy import select

    from firebid.db.engine import service_session_scope
    from firebid.db.models.core import Bid
    from firebid.services import kpis

    with service_session_scope() as session:
        bids = list(session.execute(select(Bid).order_by(Bid.human_id)).scalars())
        rows = [kpis.bid_kpis(session, bid) for bid in bids]
        stats = kpis.agent_stats(session)
    measured = [r for r in rows if r.ai_items]
    ai = sum(r.ai_items for r in measured)
    rejected = sum(r.rejected_ai_items for r in measured)
    verified = sum(r.verified_items for r in measured)
    manual = sum(r.manual_items for r in measured)
    costs = [r.agents.cost_sgd for r in rows if r.agents.runs]
    return {
        "bids": len(rows),
        "bids_with_takeoff": len(measured),
        "false_detection_rate": rejected / ai if ai else None,
        "missed_item_rate": manual / verified if verified else None,
        "unresolved_duplicates": sum(r.unresolved_duplicates for r in measured),
        "minutes_on_task": sum(r.minutes_on_task for r in rows),
        "escalation_rate": stats.escalation_rate,
        "success_rate": stats.success_rate,
        "agent_runs": stats.runs,
        "cost_sgd": float(stats.cost_sgd),
        "cost_per_bid_sgd": float(sum(costs) / len(costs)) if costs else None,
        "target_cost_per_tender_sgd": float(kpis.target_cost_per_tender() or 0) or None,
        "by_model": [{**row, "cost_sgd": float(row["cost_sgd"])} for row in stats.by_model[:10]],
    }


# --- The report -----------------------------------------------------------------------------


def report(inputs: ExitInputs, gaps: list[tuple[str, str, str]]) -> str:
    d = inputs.detection.overall
    golden = inputs.detection_golden
    source = "the golden set" if golden else "synthetic tenders only (no golden set yet)"
    lines = [
        "# Phase 1 exit report",
        "",
        f"Generated {datetime.now(UTC):%d %b %Y %H:%M} UTC by `firebid-eval exit`. Detection, "
        f"classification and mapping measured on **{source}**.",
        "",
        "## Exit criteria (requirements §13.2)",
        "",
        "| Criterion | Target | Evidence | Status |",
        "|---|---|---|---|",
    ]
    sprinklers = d.get("sprinkler_count_accuracy")
    pipes = d.get("pipe_length_error")
    lines.append(
        f"| Sprinkler count on the golden set | ≥98% | {_fmt(sprinklers)} on {source} | "
        + (
            _meets("sprinkler_count_accuracy", sprinklers)
            if golden
            else "**pending: no golden set**"
        )
        + " |"
    )
    lines.append(
        f"| Pipe length on the golden set | within ±5% | {_fmt(pipes)} error on {source} | "
        + (_meets("pipe_length_error", pipes) if golden else "**pending: no golden set**")
        + " |"
    )
    live_shadow = [s for s in inputs.shadow if not s.get("synthetic")]
    measured = [s for s in live_shadow if s.get("effort_reduction") is not None]
    effort = sum(s["effort_reduction"] for s in measured) / len(measured) if measured else None
    lines.append(
        f"| QTO effort in a shadow pilot on ≥3 live tenders | -30% | {len(live_shadow)} live "
        f"tender(s); {_fmt(effort)} mean reduction | "
        + (
            ("meets" if effort is not None and effort >= 0.30 else "**misses**")
            if len(measured) >= 3
            else "**pending: pilot not run**"
        )
        + " |"
    )

    lines += [
        "",
        "## Phase 1 KPIs (requirements §14)",
        "",
        "| KPI | Target | Synthetic | Indicative sample | Live bids | Status |",
        "|---|---|---|---|---|---|",
    ]
    sample = inputs.indicative.overall if inputs.indicative else {}
    live = inputs.live or {}
    for metric, label in LABELS.items():
        sign, target = TARGETS[metric]
        live_value = live.get(metric)
        status = _meets(metric, d.get(metric)) if golden else "pending (golden set)"
        lines.append(
            f"| {label} | {sign.replace('>=', '≥').replace('<=', '≤')}{_fmt(target)} | "
            f"{_fmt(d.get(metric))} | {_fmt(sample.get(metric)) if sample else '-'} | "
            f"{_fmt(live_value) if metric in live else '-'} | {status} |"
        )
    lines.append(
        f"| QTO effort | -30% | - | - | "
        f"{live.get('minutes_on_task', '-')} min on task recorded | pending (pilot) |"
    )
    lines.append(
        f"| Human escalation rate | monitor, trending down | - | - | "
        f"{_fmt(live.get('escalation_rate'))} of {live.get('agent_runs', 0)} runs | monitored |"
    )
    success = live.get("success_rate")
    lines.append(
        f"| Workflow and tool success | ≥98% | - | - | {_fmt(success)} | "
        + (
            "meets"
            if success is not None and success >= 0.98
            else "**misses**"
            if success is not None
            else "not measured"
        )
        + " |"
    )

    lines += ["", "### Detection, by input class and consultant", ""]
    lines += _slices(inputs.detection)
    if inputs.indicative:
        lines += [
            "",
            "### Indicative real-drawing sample",
            "",
            f"> {inputs.indicative_note}",
            "",
        ]
        lines += _slices(inputs.indicative)
    lines += [
        "",
        "### Other Phase 1 measures",
        "",
        "| Measure | Value | Target | Measured on |",
        "|---|---|---|---|",
        f"| Drawing number accuracy (FR-DOC-02) | "
        f"{_fmt(inputs.documents.overall.get('drawing_number_accuracy'))} | - | "
        f"{'golden set' if golden else 'synthetic sheets'} |",
        "| Revision accuracy (FR-DOC-02) | "
        f"{_fmt(inputs.documents.overall.get('revision_accuracy'))} "
        f"| - | {'golden set' if golden else 'synthetic sheets'} |",
        f"| Client BOQ mapping (FR-BOQ-02) | {_fmt(inputs.boq_accuracy)} | ≥90% | "
        "synthetic bill, rules only |",
    ]
    if live:
        lines += [
            "",
            "### AI cost (NFR-15)",
            "",
            f"{live['agent_runs']} agent runs across {live['bids']} bids cost SGD "
            f"{live['cost_sgd']:.2f}; per bid with runs, SGD "
            + (
                f"{live['cost_per_bid_sgd']:.2f}"
                if live.get("cost_per_bid_sgd") is not None
                else "-"
            )
            + " against a target of SGD "
            + (
                f"{live['target_cost_per_tender_sgd']:.2f}"
                if live.get("target_cost_per_tender_sgd")
                else "-"
            )
            + " (a placeholder: Phase 0 set none).",
        ]

    lines += ["", "## Shadow mode", ""]
    if inputs.shadow:
        lines += [
            "| Tender | Kind | Lines | Manual h | AI-assisted h | Effort reduction |",
            "|---|---|---|---|---|---|",
        ]
        for s in inputs.shadow:
            lines.append(
                f"| {s['tender_id']} | {'synthetic' if s.get('synthetic') else 'live'} | "
                f"{len(s['lines'])} | {s.get('manual_hours') or '-'} | "
                f"{s.get('ai_hours') or '-'} | "
                f"{_fmt(s.get('effort_reduction'))} |"
            )
    else:
        lines.append("No shadow-mode comparison recorded yet.")

    lines += [
        "",
        "## Non-functional evidence",
        "",
        "| Evidence | Status | Result |",
        "|---|---|---|",
    ]
    for evidence in inputs.evidence.values():
        if evidence.present and evidence.data is not None:
            summary = evidence.data.get("summary", "recorded")
            lines.append(f"| {evidence.name} | recorded | {summary} |")
        else:
            lines.append(f"| {evidence.name} | **pending** | not yet run |")

    lines += ["", "## Gap list", "", "| Gap | Cause | Proposed action |", "|---|---|---|"]
    lines += [f"| {gap} | {cause} | {action} |" for gap, cause, action in gaps]
    return "\n".join(lines) + "\n"


def _slices(result: SuiteResult) -> list[str]:
    metrics = list(LABELS)
    out = [
        "| Group | " + " | ".join(LABELS[m] for m in metrics) + " |",
        "|---" * (len(metrics) + 1) + "|",
    ]
    for title, groups in (
        ("input class", result.by_input_class),
        ("consultant", result.by_consultant),
    ):
        for name, values in groups.items():
            out.append(
                f"| {title}: {name} | " + " | ".join(_fmt(values.get(m)) for m in metrics) + " |"
            )
    return out


def dynamic_gaps(inputs: ExitInputs) -> list[tuple[str, str, str]]:
    """Gaps the numbers themselves show."""
    gaps: list[tuple[str, str, str]] = []
    if not inputs.detection_golden:
        gaps.append(
            (
                "No golden set: the exit accuracy criteria cannot be measured",
                "decision D3's historical tenders with verified takeoffs have not been collected",
                "collect 10-20 tenders (`firebid-eval template` / `import`); "
                "rerun `firebid-eval exit`",
            )
        )
    if inputs.indicative:
        for metric, value in inputs.indicative.overall.items():
            if metric in TARGETS and value is not None and _meets(metric, value) != "meets":
                gaps.append(
                    (
                        f"Indicative real sample: {LABELS[metric].lower()} {_fmt(value)}",
                        "see the gap entries on legend typing and head-type matching",
                        "confirm the legend on the Symbols page, then re-measure; fix matching",
                    )
                )
    for name, evidence in inputs.evidence.items():
        if not evidence.present:
            gaps.append(
                (f"{name.capitalize()}: no evidence yet", "not run", "run it (see runbooks)")
            )
    return gaps

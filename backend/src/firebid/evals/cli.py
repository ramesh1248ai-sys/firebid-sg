"""`firebid-eval`: the evaluation command line.

    firebid-eval template  --out golden_takeoff.xlsx     a blank workbook for estimators
    firebid-eval import    <workbook.xlsx>               validate a filled one
    firebid-eval generate  --out eval/synthetic          write the synthetic fixtures
    firebid-eval run       --suite synthetic             score a predictor, write a report
    firebid-eval run       --suite doc_classification    title block reading (FR-DOC-02)
    firebid-eval accept    --approver "Name"             store the current result as baseline
    firebid-eval compare   --suite synthetic             fail if anything has regressed
    firebid-eval compare-models --route <r> --models a,b  evidence for changing a model
    firebid-eval calibrate --suite p1_detection          fit detection confidence (FR-VIS-09)
    firebid-eval run       --suite p1_boq                client BOQ mapping accuracy (FR-BOQ-02)
    firebid-eval shadow    --bid <id> --workbook x.xlsx  manual takeoff beside the AI's (§13.3)
    firebid-eval exit      --out ../docs/reports/phase1-exit.md   the Phase 1 exit report
    firebid-eval corrections --out corrections.jsonl     people's corrections (FR-REV-06)

`import` and `compare` write nothing and exit non-zero when they are unhappy, which is what
makes them usable in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from firebid.evals.importer import ImportFailed, import_workbook
from firebid.evals.template import write_template

DEFAULT_SUITE = "synthetic"
# Title block reading (FR-DOC-02), scored with the real reader: see evals/doc_classification.
DOC_SUITE = "doc_classification"
DOC_METRICS = ("drawing_number_accuracy", "revision_accuracy", "sheet_classification_accuracy")
EVAL_ROOT = Path("eval")
# Phase 1 detection: sprinklers, valves and pipe (FR-VIS-03, 09), see evals/p1_detection.
DETECTION_SUITE = "p1_detection"
# Client BOQ mapping (FR-BOQ-02), see evals/p1_boq: its own report, no baseline yet.
BOQ_SUITE = "p1_boq"


def _suite_paths(suite: str, root: Path) -> tuple[Path, Path, Path]:
    """Where a suite's fixtures, latest result and baseline live."""
    return (
        root / "synthetic" / suite,
        root / "results" / f"{suite}.json",
        root / "baselines" / f"{suite}.json",
    )


def _load_golden_set(suite: str, root: Path, seed: int, tenders: int):  # type: ignore[no-untyped-def]
    """The synthetic suite is generated on demand; a real one is read from disk."""
    from firebid.evals.schema import GoldenSet
    from firebid.evals.synthetic import generate_tender

    fixtures, _, _ = _suite_paths(suite, root)
    if suite == DEFAULT_SUITE:
        generated = [
            generate_tender(
                fixtures / f"T{index:03d}",
                tender_id=f"SYNTH-{index:03d}",
                consultant=f"Synthetic Consultants {index % 3 + 1}",
                seed=seed + index,
            )
            for index in range(tenders)
        ]
        return GoldenSet(name=suite, tenders=tuple(item.truth for item in generated))

    truth_dir = root / "truth" / suite
    if not truth_dir.exists():
        raise SystemExit(
            f"no golden set at {truth_dir}. Import filled workbooks there first, or use "
            f"--suite {DEFAULT_SUITE}."
        )
    from firebid.evals.schema import TenderTruth

    tenders_found = tuple(
        TenderTruth.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(truth_dir.glob("*.json"))
    )
    if not tenders_found:
        raise SystemExit(f"{truth_dir} holds no tenders")
    return GoldenSet(name=suite, tenders=tenders_found)


def _dummy(arguments: argparse.Namespace):  # type: ignore[no-untyped-def]
    from firebid.evals.dummy import DummyPredictor

    return DummyPredictor(
        count_error=arguments.count_error,
        length_error=arguments.length_error,
        miss_rate=arguments.miss_rate,
        duplicate_recall=arguments.duplicate_recall,
        confidence=arguments.confidence,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firebid-eval", description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=EVAL_ROOT, help="where suites, results and baselines live"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    template = commands.add_parser("template", help="write a blank estimator workbook")
    template.add_argument("--out", type=Path, default=Path("golden_takeoff.xlsx"))

    importer = commands.add_parser("import", help="validate a filled estimator workbook")
    importer.add_argument("workbook", type=Path)
    importer.add_argument("--out", type=Path, default=None)

    generate = commands.add_parser("generate", help="write the synthetic fixtures")
    generate.add_argument("--out", type=Path, default=None)
    generate.add_argument("--seed", type=int, default=1)
    generate.add_argument("--tenders", type=int, default=3)

    for name, help_text in (
        ("run", "score a predictor and write a report"),
        ("accept", "store the current result as the baseline"),
        ("compare", "fail if any metric has regressed"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--suite", default=DEFAULT_SUITE)
        command.add_argument("--seed", type=int, default=1)
        command.add_argument("--tenders", type=int, default=3)
        command.add_argument("--count-error", type=float, default=0.0)
        command.add_argument("--length-error", type=float, default=0.0)
        command.add_argument("--miss-rate", type=float, default=0.0)
        command.add_argument("--duplicate-recall", type=float, default=1.0)
        command.add_argument("--confidence", type=float, default=1.0)
        if name == "accept":
            command.add_argument("--approver", required=True)
        if name == "run":
            command.add_argument("--report", type=Path, default=None)

    compare_models_command = commands.add_parser(
        "compare-models", help="evidence for changing a route's model"
    )
    compare_models_command.add_argument("--suite", default=DEFAULT_SUITE)
    compare_models_command.add_argument("--route", required=True)
    compare_models_command.add_argument("--models", required=True, help="comma-separated")
    compare_models_command.add_argument("--seed", type=int, default=1)
    compare_models_command.add_argument("--tenders", type=int, default=3)
    compare_models_command.add_argument("--report", type=Path, default=None)

    calibrate = commands.add_parser(
        "calibrate", help="fit detection confidence and check it on a hold-out set"
    )
    calibrate.add_argument("--suite", default=DETECTION_SUITE, choices=[DETECTION_SUITE])
    calibrate.add_argument("--train", type=int, default=60)
    calibrate.add_argument("--holdout", type=int, default=40)

    corrections = commands.add_parser(
        "corrections", help="export the labelled corrections people made (FR-REV-06)"
    )
    corrections.add_argument("--out", type=Path, required=True)

    shadow = commands.add_parser("shadow", help="compare a manual takeoff with the AI's")
    shadow.add_argument("--bid", required=True, help="the bid's UUID")
    shadow.add_argument("--workbook", type=Path, default=None, help="the estimator's workbook")
    shadow.add_argument(
        "--synthetic", action="store_true", help="the synthetic tender's manual takeoff"
    )
    shadow.add_argument("--hours", type=float, default=None, help="manual takeoff hours")
    shadow.add_argument("--out", type=Path, default=None)

    exit_report = commands.add_parser("exit", help="write the Phase 1 exit report")
    exit_report.add_argument("--indicative-root", type=Path, default=None)
    exit_report.add_argument("--live", action="store_true", help="include every bid's measures")
    exit_report.add_argument("--gaps", type=Path, default=Path("../docs/reports/phase1-gaps.yaml"))
    exit_report.add_argument("--out", type=Path, default=Path("../docs/reports/phase1-exit.md"))

    arguments = parser.parse_args(argv)

    if arguments.command == "shadow":
        return _run_shadow(arguments)

    if arguments.command == "exit":
        return _run_exit(arguments)

    if arguments.command == "corrections":
        return _run_corrections(arguments.out)

    if arguments.command == "calibrate":
        return _run_calibrate(arguments)

    if arguments.command == "template":
        print(f"wrote {write_template(arguments.out)}")
        return 0

    if arguments.command == "import":
        return _run_import(arguments)

    if arguments.command == "generate":
        return _run_generate(arguments)

    if arguments.command in ("run", "accept", "compare") and arguments.suite == BOQ_SUITE:
        return _run_boq(arguments)

    if arguments.command in ("run", "accept", "compare"):
        return _run_suite_command(arguments)

    return _run_compare_models(arguments)


def _metrics_for(suite: str) -> tuple[str, ...] | None:
    if suite == DOC_SUITE:
        return DOC_METRICS
    if suite == DETECTION_SUITE:
        from firebid.evals.p1_detection import METRICS

        return METRICS
    return None


def _run_boq(arguments: argparse.Namespace) -> int:
    """Mapping accuracy against the target; exits non-zero when it is missed."""
    from firebid.evals.p1_boq import evaluate

    if arguments.command != "run":
        print(f"'{arguments.command}' is not available for {BOQ_SUITE} yet", file=sys.stderr)
        return 1
    report = evaluate()
    text = report.markdown()
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(text, encoding="utf-8")
        print(f"wrote {arguments.report}")
    else:
        print(text)
    return 0 if report.met else 1


def _run_calibrate(arguments: argparse.Namespace) -> int:
    """Fit the calibration maps, write them to config, and fail if the hold-out misses."""
    from firebid.drawings.calibration import CONFIG
    from firebid.evals.detection_calibration import fit_and_check

    report = fit_and_check(train=arguments.train, holdout=arguments.holdout)
    for family, found in report["families"].items():
        print(
            f"{family}: hold-out n={found['holdout']}, calibration error {found['ece']:.3f} "
            f"(raw {found['raw_ece']:.3f})"
        )
    print(
        f"overall: {report['ece']:.3f} against a tolerance of {report['tolerance']:.2f} "
        f"-> wrote {CONFIG}"
    )
    return 0 if report["within_tolerance"] else 1


def _run_import(arguments: argparse.Namespace) -> int:
    try:
        truth = import_workbook(arguments.workbook)
    except ImportFailed as failure:
        print(failure.report(), file=sys.stderr)
        return 1

    print(
        f"{truth.tender_id}: {len(truth.sheets)} sheet(s), "
        f"{truth.total_objects()} object(s) on current revisions, "
        f"{len(truth.boq_lines)} BOQ line(s)"
    )
    if arguments.out:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(truth.model_dump_json(indent=2), encoding="utf-8")
        print(f"wrote {arguments.out}")
    return 0


def _run_generate(arguments: argparse.Namespace) -> int:
    from firebid.evals.synthetic import generate_tender

    out = arguments.out or (arguments.root / "synthetic" / DEFAULT_SUITE)
    for index in range(arguments.tenders):
        generated = generate_tender(
            out / f"T{index:03d}",
            tender_id=f"SYNTH-{index:03d}",
            seed=arguments.seed + index,
        )
        print(f"{generated.truth.tender_id}: {len(generated.sheets)} sheet(s) -> {out}")
    return 0


def _run_suite_command(arguments: argparse.Namespace) -> int:
    from firebid.evals.runner import (
        Baseline,
        compare,
        markdown_report,
        regression_report,
        run_suite,
    )

    fixtures, result_path, baseline_path = _suite_paths(arguments.suite, arguments.root)
    if arguments.suite == DOC_SUITE:
        # Title block reading, measured with the platform's own reader rather than a dummy:
        # a real golden set when one has been imported, the synthetic fixtures otherwise.
        from firebid.evals.doc_classification import TitleBlockPredictor, generate, load_golden

        suite = load_golden(arguments.root) or generate(fixtures, seed=arguments.seed)
        result = run_suite(suite.golden_set, TitleBlockPredictor(suite.files))
    elif arguments.suite == DETECTION_SUITE:
        # Detection (FR-VIS-03, 09), with the platform's own pipeline: the golden set when
        # one has been imported, synthetic installations otherwise.
        from firebid.evals import p1_detection

        detection = p1_detection.load_golden(arguments.root) or p1_detection.generate(
            fixtures, seed=arguments.seed, tenders=arguments.tenders, with_duplicates=True
        )
        result = run_suite(detection.golden_set, p1_detection.DetectionPredictor(detection))
    else:
        golden_set = _load_golden_set(
            arguments.suite, arguments.root, arguments.seed, arguments.tenders
        )
        result = run_suite(golden_set, _dummy(arguments))

    if arguments.command == "run":
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(result.to_json(), encoding="utf-8")
        synthetic = not (arguments.suite == DETECTION_SUITE and not detection.synthetic)
        report = markdown_report(result, metrics=_metrics_for(arguments.suite), synthetic=synthetic)
        if arguments.suite == DETECTION_SUITE:
            from firebid.evals.p1_detection import untyped_note

            report += untyped_note(detection)
        if arguments.report:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(report, encoding="utf-8")
            print(f"wrote {result_path} and {arguments.report}")
        else:
            print(report)
        return 0

    if arguments.command == "accept":
        written = Baseline.from_result(result, arguments.approver).write(baseline_path)
        print(f"baseline for '{arguments.suite}' accepted by {arguments.approver}: {written}")
        return 0

    # compare
    if not baseline_path.exists():
        print(
            f"no baseline for '{arguments.suite}'. Accept one first:\n"
            f'  firebid-eval accept --suite {arguments.suite} --approver "Your Name"',
            file=sys.stderr,
        )
        return 1

    regressions = compare(Baseline.read(baseline_path), result)
    report = regression_report(regressions)
    if regressions:
        print(report, file=sys.stderr)
        return 1
    print(report)
    return 0


def _run_compare_models(arguments: argparse.Namespace) -> int:
    from firebid.ai_gateway.config import get_config
    from firebid.evals.compare_models import compare_models, comparison_report
    from firebid.evals.dummy import DummyPredictor

    config = get_config()
    golden_set = _load_golden_set(
        arguments.suite, arguments.root, arguments.seed, arguments.tenders
    )

    def predictor_for(_config: object, model_name: str) -> DummyPredictor:
        # Without a real route-backed predictor this measures the harness, not the models.
        # P1-02 replaces this with one that actually calls the gateway.
        return DummyPredictor(name=f"dummy[{model_name}]", version="1")

    comparison = compare_models(
        config,
        golden_set,
        arguments.route,
        [name.strip() for name in arguments.models.split(",") if name.strip()],
        predictor_for,
    )
    report = comparison_report(comparison)
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(report, encoding="utf-8")
        print(f"wrote {arguments.report}")
    else:
        print(report)

    # A candidate that could not run is worth a non-zero exit: it usually means a data-class
    # approval is missing, which someone needs to see.
    return 1 if any(candidate.refused_because for candidate in comparison.candidates) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _run_shadow(arguments: argparse.Namespace) -> int:
    """A manual takeoff beside the bid's verified AI-assisted one, with the effort of each."""
    import json
    import uuid

    from firebid.db.engine import service_session_scope
    from firebid.evals import shadow
    from firebid.services import effort

    if arguments.synthetic:
        truth = shadow.synthetic_manual_takeoff()
    elif arguments.workbook:
        try:
            truth = import_workbook(arguments.workbook)
        except ImportFailed as failure:
            print(failure.report(), file=sys.stderr)
            return 1
    else:
        print("give --workbook, or --synthetic", file=sys.stderr)
        return 1
    bid = uuid.UUID(arguments.bid)
    with service_session_scope() as session:
        items = shadow.ai_takeoff(session, bid)
        worked = effort.time_on_task(session, bid)
    found = shadow.compare(
        truth,
        items,
        manual_hours=arguments.hours,
        ai_hours=worked.hours,
        synthetic=arguments.synthetic,
    )
    results = arguments.root / "results" / "shadow"
    results.mkdir(parents=True, exist_ok=True)
    (results / f"{truth.tender_id}.json").write_text(
        json.dumps(found.to_json(), indent=1, default=str), encoding="utf-8"
    )
    out = arguments.out or (results / f"{truth.tender_id}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(found.markdown(), encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(found.lines)} lines)")
    return 0


def _run_exit(arguments: argparse.Namespace) -> int:
    import yaml

    from firebid.evals import p1_exit

    inputs = p1_exit.gather(arguments.root, arguments.indicative_root, arguments.live)
    curated = (
        yaml.safe_load(arguments.gaps.read_text(encoding="utf-8"))
        if arguments.gaps.exists()
        else {}
    )
    gaps = p1_exit.dynamic_gaps(inputs) + [
        (g["gap"], " ".join(str(g["cause"]).split()), " ".join(str(g["action"]).split()))
        for g in (curated or {}).get("gaps", [])
    ]
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(p1_exit.report(inputs, gaps), encoding="utf-8", newline="\n")
    print(f"wrote {arguments.out}")
    return 0


def _run_corrections(out: Path) -> int:
    """Every bid's corrections as JSON lines: derived labels only (requirements §11.4).

    Read on the service role, as a cross-bid dataset is: what was proposed, what a person
    made of it, why, and which detector, calibration and rule proposed it.
    """
    import json

    from firebid.db.engine import service_session_scope
    from firebid.services.review_actions import correction_rows

    with service_session_scope() as session:
        rows = correction_rows(session)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} corrections to {out}")
    return 0

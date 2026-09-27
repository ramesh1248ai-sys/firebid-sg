"""`firebid-eval`: the evaluation command line.

    firebid-eval template  --out golden_takeoff.xlsx     a blank workbook for estimators
    firebid-eval import    <workbook.xlsx>               validate a filled one
    firebid-eval generate  --out eval/synthetic          write the synthetic fixtures
    firebid-eval run       --suite synthetic             score a predictor, write a report
    firebid-eval run       --suite doc_classification    title block reading (FR-DOC-02)
    firebid-eval accept    --approver "Name"             store the current result as baseline
    firebid-eval compare   --suite synthetic             fail if anything has regressed
    firebid-eval compare-models --route <r> --models a,b  evidence for changing a model

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

    arguments = parser.parse_args(argv)

    if arguments.command == "template":
        print(f"wrote {write_template(arguments.out)}")
        return 0

    if arguments.command == "import":
        return _run_import(arguments)

    if arguments.command == "generate":
        return _run_generate(arguments)

    if arguments.command in ("run", "accept", "compare"):
        return _run_suite_command(arguments)

    return _run_compare_models(arguments)


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
    else:
        golden_set = _load_golden_set(
            arguments.suite, arguments.root, arguments.seed, arguments.tenders
        )
        result = run_suite(golden_set, _dummy(arguments))

    if arguments.command == "run":
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(result.to_json(), encoding="utf-8")
        report = markdown_report(
            result, metrics=DOC_METRICS if arguments.suite == DOC_SUITE else None
        )
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

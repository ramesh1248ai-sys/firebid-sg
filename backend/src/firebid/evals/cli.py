"""`firebid-eval`: the evaluation command line.

Two commands so far, both serving the golden-set collection (decision D3):

    firebid-eval template --out golden_takeoff.xlsx    write a blank workbook for estimators
    firebid-eval import <workbook.xlsx> [--out x.json]  validate a filled one

`import` writes nothing unless the workbook is clean. A partial import is worse than none: it
looks like it worked, and the gap only shows up as an accuracy number nobody can explain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from firebid.evals.importer import ImportFailed, import_workbook
from firebid.evals.template import write_template


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firebid-eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    template = commands.add_parser("template", help="write a blank estimator workbook")
    template.add_argument(
        "--out", type=Path, default=Path("golden_takeoff.xlsx"), help="where to write it"
    )

    importer = commands.add_parser("import", help="validate a filled estimator workbook")
    importer.add_argument("workbook", type=Path)
    importer.add_argument(
        "--out", type=Path, default=None, help="write the normalised JSON here on success"
    )

    arguments = parser.parse_args(argv)

    if arguments.command == "template":
        written = write_template(arguments.out)
        print(f"wrote {written}")
        return 0

    try:
        truth = import_workbook(arguments.workbook)
    except ImportFailed as failure:
        # The report goes to stderr so `--out` piping stays clean.
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""The Phase 2 systems suite: equipment by type, and its pipe by size (FR-VIS-04, FR-QTO-06).

    firebid-eval run     --suite p2_systems [--report eval/results/p2_systems.md]
    firebid-eval compare --suite p2_systems

Scored with the platform's own pipeline, as `p1_detection` is: geometry and views, legends
and symbols, the pipe network and its sizes, and de-duplication across sheets. Against:

* the **golden set** when one exists: truth in `eval/truth/p2_systems/<tender>.json`,
  drawings in `eval/files/p2_systems/<tender>/`, one file per sheet named by its drawing
  number;
* otherwise the **synthetic tender** of `synthetic_systems`: a pump room, a riser
  schematic, a typical floor and a site hydrant plan.

Legend rows are typed by the keyword rules alone, on both: the synthetic legend is written
in the words consultants use, so the rules are measured too.

Equipment found by its confidence is not reported here: the calibration was fitted on
sprinkler installations, and is refitted once a golden set gives equipment outcomes.
"""

from __future__ import annotations

from pathlib import Path

from firebid.evals import synthetic
from firebid.evals import synthetic_systems as fixture
from firebid.evals.p1_detection import EVAL_TYPES, Suite
from firebid.evals.p1_detection import load_golden as _load_golden
from firebid.evals.schema import (
    GoldenSet,
    InputClass,
    ObjectCount,
    ObjectType,
    PipeLength,
    RevisionStatus,
    SheetTruth,
    TenderTruth,
)

SUITE = "p2_systems"
METRICS = (
    "equipment_count_accuracy",
    "pipe_length_error",
    "missed_item_rate",
    "false_detection_rate",
    "duplicate_detection_rate",
)
TENDER = "SYS-001"


def load_golden(root: Path) -> Suite | None:
    return _load_golden(root, SUITE)


def generate(out_dir: Path) -> Suite:
    """The synthetic tender, written as DXF, with each sheet's truth."""
    folder = out_dir / TENDER
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    sheets = []
    for document, drawn in fixture.tender():
        paths.append(synthetic.write_dxf(document, folder / f"{drawn.number}.dxf"))
        sheets.append(
            SheetTruth(
                sheet_number=drawn.number,
                revision="R01",
                status=RevisionStatus.CURRENT,
                input_class=InputClass.DWG,
                not_to_scale=not drawn.measured,
                counts=tuple(
                    ObjectCount(object_type=ObjectType(kind), count=count)
                    for kind, count in sorted(drawn.counts.items())
                    if kind in EVAL_TYPES
                ),
                pipe_lengths=tuple(
                    PipeLength(nominal_diameter_mm=dn, length_mm=round(value))
                    for dn, value in sorted(drawn.lengths.items())
                )
                if drawn.measured
                else (),
                duplicates_of=drawn.duplicates_of,
            )
        )
    truth = TenderTruth(
        tender_id=TENDER,
        consultant=fixture.DELTA.name,
        input_class=InputClass.DWG,
        sheets=tuple(sheets),
    )
    return Suite(GoldenSet(name=SUITE, tenders=(truth,)), {TENDER: paths}, synthetic=True)

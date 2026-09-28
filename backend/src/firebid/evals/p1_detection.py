"""The Phase 1 detection suite: sprinklers by type, valves by type, pipe length by DN.

    firebid-eval run --suite p1_detection [--report eval/results/p1_detection.md]

Scores the platform's own detection (P1-03 geometry and views, P1-04 legends and symbols,
P1-05 network, sizes and calibration) as a predictor, against:

* the **golden set** when one exists: truth in `eval/truth/p1_detection/<tender>.json`
  (from `firebid-eval import`), drawings in `eval/files/p1_detection/<tender>/`, one file per
  sheet, named by its drawing number (`FP-L05-201.pdf`);
* otherwise **synthetic installations**, generated per seed.

A tender's legend may be on any of its sheets, so legend rows are collected from all of them
first, and every sheet's symbols are matched against that tender-wide legend. What a legend
row is comes from the keyword rules alone on the golden set, as no person has confirmed
anything; rows the rules cannot type are left out of the counts and reported. The synthetic
tenders stand in for one confirmation pass with their known answers.

The Phase 1 targets (requirements §14) are sprinkler count accuracy at least 98% and pipe
length within ±5%; `firebid.evals.runner` reports against them.

Duplicates (FR-QTO-08): each tender's sheets are passed through the QTO engine's
de-duplication (P1-07), and the sheets it finds repeating another are scored against the
truth's `duplicates_of`, for the ≥95% target. With no golden set, two synthetic tenders
seed them: a general arrangement with an enlarged plan and a riser schematic, and a
match-lined pair of plans.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from firebid.drawings import calibration, geometry, legends, symbols
from firebid.drawings.detection import TypeInfo, detect, views_of
from firebid.drawings.pipe_network import Placed
from firebid.drawings.symbol_rules import load as load_rules
from firebid.drawings.views import _title_block_region
from firebid.evals import synthetic
from firebid.evals.prediction import (
    CountPrediction,
    LengthPrediction,
    SheetPrediction,
    TenderPrediction,
)
from firebid.evals.qto_pipeline import Sheet as QtoSheet
from firebid.evals.qto_pipeline import collect
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
from firebid.evals.synthetic_network import DESCRIBED, NETWORK, network_plan

SUITE = "p1_detection"
METRICS = (
    "sprinkler_count_accuracy",
    "pipe_length_error",
    "missed_item_rate",
    "false_detection_rate",
    "calibration_error",
    "duplicate_detection_rate",
)
EVAL_TYPES = {item.value for item in ObjectType}


@dataclass
class Suite:
    golden_set: GoldenSet
    files: dict[str, list[Path]]
    synthetic: bool
    # Filled by the predictor: legend rows it could not type, per tender.
    untyped: dict[str, list[str]] = field(default_factory=dict)


def load_golden(root: Path) -> Suite | None:
    truth_dir = root / "truth" / SUITE
    if not truth_dir.exists() or not any(truth_dir.glob("*.json")):
        return None
    tenders = tuple(
        TenderTruth.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(truth_dir.glob("*.json"))
    )
    files = {
        tender.tender_id: sorted(
            path
            for path in (root / "files" / SUITE / tender.tender_id).glob("*")
            if path.suffix.lower() in (".pdf", ".dxf")
        )
        for tender in tenders
    }
    return Suite(GoldenSet(name=SUITE, tenders=tenders), files, synthetic=False)


def generate(
    out_dir: Path, seed: int = 1, tenders: int = 3, with_duplicates: bool = False
) -> Suite:
    """Synthetic tenders: one installation each, alternating DXF and vector PDF.

    `with_duplicates` adds the two tenders whose sheets repeat each other.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    truths = []
    files: dict[str, list[Path]] = {}
    for index in range(tenders):
        tender_id = f"DET-{seed + index:03d}"
        document, truth = network_plan(seed=seed + index, jitter=20.0)
        folder = out_dir / tender_id
        folder.mkdir(parents=True, exist_ok=True)
        pdf = index % 2 == 1
        path = (
            synthetic.write_pdf(document, folder / "FP-L05-201.pdf", live_text=True)
            if pdf
            else synthetic.write_dxf(document, folder / "FP-L05-201.dxf")
        )
        files[tender_id] = [path]
        counts = tuple(
            ObjectCount(object_type=ObjectType(kind), count=count)
            for kind, count in sorted(truth.counts.items())
            if kind in EVAL_TYPES
        )
        lengths = tuple(
            PipeLength(nominal_diameter_mm=dn, length_mm=round(value))
            for dn, value in sorted(truth.lengths.items())
        )
        truths.append(
            TenderTruth(
                tender_id=tender_id,
                consultant=NETWORK.name,
                input_class=InputClass.VECTOR_PDF if pdf else InputClass.DWG,
                sheets=(
                    SheetTruth(
                        sheet_number="FP-L05-201",
                        revision="R01",
                        status=RevisionStatus.CURRENT,
                        input_class=InputClass.VECTOR_PDF if pdf else InputClass.DWG,
                        counts=counts,
                        pipe_lengths=lengths,
                    ),
                ),
            )
        )
    if with_duplicates:
        for repeated, paths in _duplicate_tenders(out_dir, seed):
            truths.append(repeated)
            files[repeated.tender_id] = paths
    return Suite(GoldenSet(name=SUITE, tenders=tuple(truths)), files, synthetic=True)


def _duplicate_tenders(out_dir: Path, seed: int) -> list[tuple[TenderTruth, list[Path]]]:
    """Tenders whose sheets repeat each other (FR-QTO-08), with each sheet's own truth."""
    from firebid.evals import synthetic_qto as fixture

    west, east = fixture.match_lined_pair()
    sets = {
        f"DUP-{seed:03d}": [
            ("FP-L05-201", fixture.general_arrangement(), (), True),
            # 1:50 with nothing to verify its scale against: counted, not measured.
            ("FP-L05-301", fixture.enlarged_plan(), ("FP-L05-201",), False),
            ("FP-SCH-001", fixture.riser_schematic(), ("FP-L05-201",), False),
        ],
        f"DUP-{seed + 1:03d}": [
            ("FP-L05-202", west, (), True),
            ("FP-L05-203", east, ("FP-L05-202",), True),
        ],
    }
    out = []
    for tender_id, sheets in sets.items():
        folder = out_dir / tender_id
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        sheet_truths = []
        for number, (document, drawn), duplicates_of, measured in sheets:
            paths.append(synthetic.write_dxf(document, folder / f"{number}.dxf"))
            lengths: dict[int, float] = defaultdict(float)
            for pipe in drawn.pipes:
                lengths[pipe.dn] += abs(pipe.x1 - pipe.x0) + abs(pipe.y1 - pipe.y0)
            sheet_truths.append(
                SheetTruth(
                    sheet_number=number,
                    revision="R01",
                    status=RevisionStatus.CURRENT,
                    input_class=InputClass.DWG,
                    counts=tuple(
                        ObjectCount(object_type=ObjectType(kind), count=count)
                        for kind, count in sorted(fixture.counted(drawn).items())
                        if kind in EVAL_TYPES
                    ),
                    pipe_lengths=tuple(
                        PipeLength(nominal_diameter_mm=dn, length_mm=round(value))
                        for dn, value in sorted(lengths.items())
                    )
                    if measured
                    else (),
                    duplicates_of=duplicates_of,
                )
            )
        truth = TenderTruth(
            tender_id=tender_id,
            consultant=NETWORK.name,
            input_class=InputClass.DWG,
            sheets=tuple(sheet_truths),
        )
        out.append((truth, paths))
    return out


def _types() -> dict[str, Any]:
    from firebid.services.object_library import seed_types

    return {kind.key: kind for kind in seed_types()}


class DetectionPredictor:
    """The platform's P1-05 detection, scored as a predictor."""

    name = "p1-detection"

    def __init__(self, suite: Suite) -> None:
        self._suite = suite
        self._kinds = _types()
        self._rules = load_rules()
        self._calibration = calibration.load()
        self.version = f"detector-{self._calibration.version}"

    def _type_of(self, description: str) -> TypeInfo | None:
        key = DESCRIBED.get(description) if self._suite.synthetic else None
        if key is None:
            proposal = self._rules.propose(description)
            key = proposal.object_type if proposal else None
        if key is None or key not in self._kinds:
            return None
        kind = self._kinds[key]
        extra = {"fitting": "reducer"} if key == "fitting" else {}
        return TypeInfo(kind.key, kind.category, kind.measure, extra)

    def predict(self, truth: TenderTruth) -> TenderPrediction:
        read = [_read(path) for path in self._suite.files.get(truth.tender_id, [])]
        sheets = [sheet for sheet in read if sheet is not None]
        # The tender's legend, wherever it is drawn.
        rows: list[Any] = []
        for sheet in sheets:
            for legend in sheet["legends"]:
                rows.extend(row for row in legend.rows if row.symbol.signature is not None)
        untyped = sorted(
            {row.description for row in rows if self._type_of(row.description) is None}
        )
        self._suite.untyped[truth.tender_id] = untyped
        predictions = []
        detections: list[Any] = []
        runs: list[Any] = []
        for sheet in sheets:
            placed = self._placed(sheet, rows)
            found = detect(sheet["table"], placed, sheet["views"], excluded=sheet["excluded"])
            calibration.calibrate(found, self._calibration)
            predictions.append(self._sheet(sheet["number"], found))
            collect(QtoSheet(sheet["number"], None), found, detections, runs)
        from firebid.qto import dedup

        groups = dedup.find(detections, runs)
        repeats = duplicates(detections, groups)
        # A run left unsized on its sheet, sized across a match line, as takeoff sizes it.
        carried: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        sized = dedup.carried_sizes(groups)
        for run in runs:
            if run.id in sized and run.length_mm is not None:
                carried[run.at.sheet_number][sized[run.id][0]] += run.length_mm
        return TenderPrediction(
            tender_id=truth.tender_id,
            sheets=tuple(
                _with_carried(p, carried.get(p.sheet_number, {})).model_copy(
                    update={"duplicates_of": tuple(sorted(repeats.get(p.sheet_number, ())))}
                )
                for p in predictions
            ),
        )

    def _placed(self, sheet: dict[str, Any], rows: list[Any]) -> list[Placed]:
        references = [row.symbol.signature for row in rows]
        placed = []
        for cluster in symbols.clusters(sheet["table"], excluding=sheet["excluded"]):
            if cluster.signature is None:
                continue
            match = symbols.best_match(cluster.signature, references)
            if match is None:
                continue
            row = rows[match.index]
            kind = self._type_of(row.description)
            if kind is None:
                continue
            cx, cy = cluster.centre
            placed.append(
                Placed(
                    object_type=kind.object_type,
                    category=kind.category,
                    measure=kind.measure,
                    cx=cx,
                    cy=cy,
                    box=cluster.box,
                    rotation=cluster.rotation,
                    method=match.how,
                    match_distance=match.distance,
                    tolerance=row.symbol.signature.tolerance,
                    attributes=dict(kind.attributes),
                    rows=cluster.rows,
                    description=row.description,
                )
            )
        return placed

    def _sheet(self, number: str, found: Any) -> SheetPrediction:
        by_type: dict[str, list[float]] = defaultdict(list)
        for item in found.objects:
            if item.kind == "object" and item.object_type in EVAL_TYPES:
                by_type[item.object_type].append(item.calibrated_confidence or 0.0)
        by_dn: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for run in found.runs:
            if run.dn is not None and run.length_mm is not None:
                by_dn[run.dn].append((run.length_mm, run.calibrated_confidence or 0.0))
        return SheetPrediction(
            sheet_number=number,
            revision="R01",
            counts=tuple(
                CountPrediction(
                    object_type=ObjectType(kind),
                    count=len(values),
                    confidence=round(mean(values), 4),
                )
                for kind, values in sorted(by_type.items())
            ),
            pipe_lengths=tuple(
                LengthPrediction(
                    nominal_diameter_mm=dn,
                    length_mm=round(sum(length for length, _ in values)),
                    confidence=round(mean(c for _, c in values), 4),
                )
                for dn, values in sorted(by_dn.items())
            ),
        )


def _with_carried(sheet: SheetPrediction, carried: dict[int, int]) -> SheetPrediction:
    if not carried:
        return sheet
    lengths = {entry.nominal_diameter_mm: entry for entry in sheet.pipe_lengths}
    for dn, extra in carried.items():
        entry = lengths.get(dn)
        lengths[dn] = (
            entry.model_copy(update={"length_mm": entry.length_mm + extra})
            if entry
            else LengthPrediction(nominal_diameter_mm=dn, length_mm=extra, confidence=0.5)
        )
    return sheet.model_copy(update={"pipe_lengths": tuple(lengths[dn] for dn in sorted(lengths))})


def duplicates(detections: list[Any], groups: list[Any]) -> dict[str, set[str]]:
    """Which sheets the QTO engine finds repeating which (P1-07), as the truth states it.

    A plan pair: the sheet not counted repeats the one counted. A schematic or section
    repeats the general plans that show the same kinds of object.
    """
    out: dict[str, set[str]] = defaultdict(set)
    plans: dict[str, set[str]] = defaultdict(set)
    for detection in detections:
        if detection.at.view_kind in ("plan", None):
            plans[detection.at.sheet_number].add(detection.object_type)
    for group in groups:
        if group.kind == "schematic":
            shown = {m.get("object_type") for m in group.members} - {None}
            for member in group.members:
                out[member["sheet_number"]].update(
                    number for number, kinds in plans.items() if kinds & shown
                )
            continue
        kept = {m["sheet_number"] for m in group.members if m["keep"]}
        for member in group.members:
            if not member["keep"]:
                out[member["sheet_number"]].update(kept - {member["sheet_number"]})
    return out


def _read(path: Path) -> dict[str, Any] | None:
    """One drawing file's geometry, views, legends and no-go regions, in-process."""
    from firebid.parsing import geometry_pdf
    from firebid.parsing.geometry_dxf import extract as extract_dxf

    if path.suffix.lower() == ".dxf":
        result = extract_dxf(path.read_bytes(), None)
    elif path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        payload = path.read_bytes()
        document = pdfium.PdfDocument(payload)
        width, height = document[0].get_size()
        document.close()
        result = {
            **geometry_pdf.extract(payload, 0),
            "page": [0.0, 0.0, width * 25.4 / 72, height * 25.4 / 72],
        }
    else:
        return None
    table = geometry.from_parquet(result["parquet"])
    page = (result["page"][0], result["page"][1], result["page"][2], result["page"][3])
    found = legends.detect(table, page)
    excluded = [legend.box for legend in found]
    region = _title_block_region(geometry.texts(table), page)
    if region is not None:
        excluded.append((region.x0, region.y0, region.x1, region.y1))
    return {
        "number": path.stem,
        "table": table,
        "views": views_of(table, page, None, result.get("views")),
        "legends": found,
        "excluded": excluded,
    }


def untyped_note(suite: Suite) -> str:
    """The report's note on legend rows no rule could type (golden set only)."""
    missing = {tender: rows for tender, rows in suite.untyped.items() if rows}
    if not missing:
        return ""
    return (
        "\n## Legend rows not typed by the keyword rules\n\n"
        "Not counted: on the golden set no person has confirmed a mapping, so only the rules "
        "type rows.\n\n" + json.dumps(missing, indent=2) + "\n"
    )

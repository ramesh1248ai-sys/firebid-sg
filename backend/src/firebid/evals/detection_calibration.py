"""Fitting and checking the detection calibration (FR-VIS-09, P1-05 build item 7).

Detections are only calibrated against outcomes, so this makes outcomes: synthetic
installations with realistic mistakes in them (near-miss symbols on and off the network,
decoy symbols in a detail, missing, wrong and contradictory size annotations, placement
jitter), where every detection can be marked right or wrong. The golden set joins them when
it has detections with verified outcomes.

Seeds are split: the maps are fitted on the training seeds and the expected calibration
error is measured on hold-out seeds they never saw. The DXF form is used, for speed; the
PDF form produces the same geometry (P1-03), so the same scores.

    firebid-eval calibrate --suite p1_detection [--train 40] [--holdout 20]
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from firebid.drawings import calibration, geometry
from firebid.drawings.detection import TypeInfo, detect, placed_from_legend, views_of
from firebid.evals import synthetic
from firebid.evals.metrics import calibration as ece
from firebid.evals.synthetic_network import DESCRIBED, TYPES, network_plan
from firebid.parsing.geometry_dxf import extract as extract_dxf

# The configured tolerance for expected calibration error on the hold-out set.
ECE_TOLERANCE = 0.05
# A detection is at the place of an installed symbol when within this, on paper.
SAME_PLACE_MM = 1.0


@dataclass(frozen=True)
class Outcome:
    family: str
    raw: float
    correct: bool


def _kinds() -> dict[str, Any]:
    from firebid.services.object_library import seed_types

    return {kind.key: kind for kind in seed_types()}


def type_of_factory() -> Any:
    kinds = _kinds()

    def type_of(description: str) -> TypeInfo | None:
        key = DESCRIBED.get(description)
        if key is None:
            return None
        kind = kinds[key]
        return TypeInfo(
            kind.key,
            kind.category,
            kind.measure,
            {"fitting": "reducer"} if key == "fitting" else {},
        )

    return type_of


def outcomes_for(seed: int, type_of: Any | None = None) -> list[Outcome]:
    """Every detection on one noisy synthetic plan, marked right or wrong."""
    rng = random.Random(seed)  # noqa: S311  # reproducible fixtures, not cryptography
    document, _ = network_plan(
        jitter=30.0,
        distractors=rng.randint(0, 6),
        near_misses=rng.randint(4, 10),
        label_noise=0.5,
        seed=seed,
    )
    result = extract_dxf(synthetic.dxf_bytes(document), None)
    table = geometry.from_parquet(result["parquet"])
    page = tuple(result["page"])
    views = views_of(table, page, "1:100", result.get("views"))
    placed, excluded = placed_from_legend(table, page, type_of or type_of_factory())
    found = detect(table, placed, views, excluded=excluded)

    installed = _installed(table, excluded)
    reducer_x = next((x for x, _, kind in installed if kind == "fitting"), math.inf)
    outcomes: list[Outcome] = []
    right_symbol: dict[int, bool] = {}
    for item in found.objects:
        if item.kind == "drop":
            continue
        correct = any(
            kind == item.object_type and math.dist((x, y), (item.x, item.y)) <= SAME_PLACE_MM
            for x, y, kind in installed
        )
        right_symbol[id(item)] = correct
        outcomes.append(Outcome("symbol", item.raw_confidence, correct))
    runs = {run.run_id: run for run in found.runs}
    for item in found.objects:
        if item.kind != "drop":
            continue
        head = next(
            (o for o in found.objects if o.kind == "object" and o.x == item.x and o.y == item.y),
            None,
        )
        run_id = item.evidence.get("run")
        run = runs.get(int(run_id)) if run_id is not None else None
        correct = bool(
            head is not None and right_symbol.get(id(head)) and run is not None and run.dn == 50
        )
        outcomes.append(Outcome("drop", item.raw_confidence, correct))
    for run in found.runs:
        xs = [p[0] for p in run.points]
        ys = [p[1] for p in run.points]
        vertical = max(ys) - min(ys) > max(xs) - min(xs)
        expected_dn = 50 if vertical else (150 if max(xs) <= reducer_x + 0.5 else 100)
        expected_class = "branch" if vertical else "main"
        correct = run.dn == expected_dn and run.run_class == expected_class
        outcomes.append(Outcome("run", run.raw_confidence, correct))
    return outcomes


def _installed(
    table: Any, excluded: list[tuple[float, float, float, float]]
) -> list[tuple[float, float, str]]:
    """Where the installed symbols are: inserts on the symbol layer, outside the legend."""
    from firebid.evals.synthetic_symbols import LAYER_SYMBOL

    rows = table.select(["kind", "layer", "block", "minx", "miny", "maxx", "maxy"]).to_pylist()
    found = []
    for row in rows:
        if row["kind"] != "insert" or row["layer"] != LAYER_SYMBOL or row["block"] not in TYPES:
            continue
        cx, cy = (row["minx"] + row["maxx"]) / 2, (row["miny"] + row["maxy"]) / 2
        if any(box[0] <= cx <= box[2] and box[1] <= cy <= box[3] for box in excluded):
            continue
        found.append((cx, cy, TYPES[row["block"]]))
    return found


def fit_and_check(train: int = 40, holdout: int = 20, start: int = 1_000) -> dict[str, Any]:
    """Fit on `train` seeds, measure on `holdout` others, and write the calibration file."""
    type_of = type_of_factory()
    fitted_on = [o for seed in range(start, start + train) for o in outcomes_for(seed, type_of)]
    held_out = [
        o
        for seed in range(start + train, start + train + holdout)
        for o in outcomes_for(seed, type_of)
    ]
    maps = {}
    report: dict[str, Any] = {
        "train_seeds": [start, start + train - 1],
        "holdout_seeds": [start + train, start + train + holdout - 1],
        "tolerance": ECE_TOLERANCE,
        "families": {},
    }
    calibrated_all: list[tuple[float, bool]] = []
    raw_all: list[tuple[float, bool]] = []
    for family in calibration.FAMILIES:
        train_rows = [o for o in fitted_on if o.family == family]
        maps[family] = calibration.fit([o.raw for o in train_rows], [o.correct for o in train_rows])
        test_rows = [o for o in held_out if o.family == family]
        calibrated = [(maps[family](o.raw), o.correct) for o in test_rows]
        raw = [(o.raw, o.correct) for o in test_rows]
        calibrated_all += calibrated
        raw_all += raw
        value, bins = ece(calibrated)
        report["families"][family] = {
            "train": len(train_rows),
            "holdout": len(test_rows),
            "accuracy": round(sum(o.correct for o in test_rows) / max(len(test_rows), 1), 4),
            "raw_ece": round(ece(raw)[0].value or 0.0, 4),
            "ece": round(value.value or 0.0, 4),
            "reliability": [
                {
                    "from": b.lower,
                    "to": b.upper,
                    "n": b.count,
                    "said": round(b.mean_confidence, 3),
                    "was": round(b.accuracy, 3),
                }
                for b in bins
            ],
        }
    report["ece"] = round(ece(calibrated_all)[0].value or 0.0, 4)
    report["raw_ece"] = round(ece(raw_all)[0].value or 0.0, 4)
    report["within_tolerance"] = report["ece"] <= ECE_TOLERANCE
    calibration.save(maps, report)
    return report

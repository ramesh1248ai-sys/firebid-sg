"""Calibrated confidence: what a raw detection score means in how often it is right (FR-VIS-09).

A raw score (`detection.symbol_score`, `run_score`) orders detections from doubtful to sure,
but its values mean nothing yet: 0.9 might be right 99% of the time or 60%. Isotonic
regression, fitted on detections whose outcome is known (the golden set and synthetic
installations with mistakes in them), maps each raw score to the share of detections with
that score that were right. The map only ever rises, so ordering is kept.

Each family has its own map, because their raw scores come from different features:
`symbol` (objects and risers), `drop` and `run`. The fitted maps are a versioned file,
`config/calibration/p1_detection.json`, written by `firebid-eval calibrate` and never by
hand. Every stored detection records the calibration version it was scored with.

Fitting is pool-adjacent-violators in NumPy: no dependency for twenty lines of arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

CONFIG = Path(__file__).resolve().parents[3] / "config" / "calibration" / "p1_detection.json"
FAMILIES = ("symbol", "drop", "run")


@dataclass(frozen=True)
class Isotonic:
    """A rising step map from raw score to probability, interpolated between block centres."""

    x: tuple[float, ...]
    y: tuple[float, ...]

    def __call__(self, raw: float) -> float:
        if not self.x:
            return float(raw)
        return float(np.clip(np.interp(raw, self.x, self.y), 0.0, 1.0))

    def as_json(self) -> dict[str, list[float]]:
        return {"x": [round(v, 6) for v in self.x], "y": [round(v, 6) for v in self.y]}


def fit(raw: list[float], correct: list[bool]) -> Isotonic:
    """Pool-adjacent-violators: the closest rising fit to the outcomes, by least squares."""
    if not raw:
        return Isotonic((), ())
    # Equal scores are one point: many detections share a score exactly (every labelled
    # branch scores the same), and splitting them into blocks at one x would let the map
    # take whichever block came last, rather than their share right.
    values, index = np.unique(np.asarray(raw, dtype=float), return_inverse=True)
    rights = np.bincount(index, weights=np.asarray(correct, dtype=float))
    counts = np.bincount(index).astype(float)
    # Each block: (sum of y, count, sum of x weighted by count).
    blocks: list[list[float]] = []
    for x, right, count in zip(values, rights, counts, strict=True):
        blocks.append([right, count, x * count])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            top = blocks.pop()
            blocks[-1] = [blocks[-1][0] + top[0], blocks[-1][1] + top[1], blocks[-1][2] + top[2]]
    centres = tuple(float(total_x / count) for _, count, total_x in blocks)
    means = tuple(float(total_y / count) for total_y, count, _ in blocks)
    return Isotonic(centres, means)


@dataclass(frozen=True)
class Calibration:
    maps: dict[str, Isotonic]
    version: str
    report: dict[str, Any]

    def apply(self, family: str, raw: float) -> float:
        found = self.maps.get(family)
        return found(raw) if found is not None else float(raw)


def save(maps: dict[str, Isotonic], report: dict[str, Any], path: Path = CONFIG) -> Calibration:
    body = {"families": {name: fitted.as_json() for name, fitted in maps.items()}}
    version = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version, **body, "report": report}, indent=2) + "\n",
        encoding="utf-8",
    )
    return Calibration(maps, version, report)


def load(path: Path = CONFIG) -> Calibration:
    """The fitted calibration, or an identity map marked `uncalibrated` if none is fitted."""
    if not path.exists():
        return Calibration({}, "uncalibrated", {})
    data = json.loads(path.read_text(encoding="utf-8"))
    maps = {
        name: Isotonic(tuple(fitted["x"]), tuple(fitted["y"]))
        for name, fitted in data.get("families", {}).items()
    }
    return Calibration(maps, str(data.get("version", "unknown")), dict(data.get("report", {})))


def calibrate(detections: Any, calibration: Calibration) -> None:
    """Set every detection's calibrated confidence, in place."""
    for item in detections.objects:
        family = "drop" if item.kind == "drop" else "symbol"
        item.calibrated_confidence = round(calibration.apply(family, item.raw_confidence), 4)
    for run in detections.runs:
        run.calibrated_confidence = round(calibration.apply("run", run.raw_confidence), 4)

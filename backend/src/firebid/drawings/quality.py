"""How far each sheet's source can be trusted, said up front (FR-DOC-06).

A clean vector export and a 72 dpi scan of a fax are both "a sheet", and a takeoff from the
second will miss things however good the detector is. So every sheet gets an expected
accuracy band, high, medium or low, and a "manual takeoff recommended" flag when it is a scan
below the floor. The register and the viewer show both, so the estimator knows before they
start which sheets to check by hand.

Pure: measures in, verdict out, with the reasons. The thresholds are configuration
(`config/input_quality.yaml`), dated, because they are policy the pilot will move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "input_quality.yaml"


@dataclass(frozen=True)
class Policy:
    effective_from: date
    good_dpi: float
    good_ocr: float
    floor_dpi: float
    floor_ocr: float
    bands: dict[str, str]


@dataclass(frozen=True)
class Measures:
    content: str  # vector | mixed | raster | empty
    dpi: float | None = None
    ocr: float | None = None
    scale: str = "absent"  # stated | not_to_scale | absent


@dataclass(frozen=True)
class Verdict:
    band: str  # high | medium | low
    manual_takeoff_recommended: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    expectation: str = ""


def scale_state(scale_text: str | None) -> str:
    if not scale_text:
        return "absent"
    if scale_text.upper().replace(".", "").replace(" ", "") in ("NTS", "NOTTOSCALE"):
        return "not_to_scale"
    return "stated" if scale_text.strip().startswith("1:") else "absent"


def assess(measures: Measures, policy: Policy | None = None) -> Verdict:
    policy = policy or current_policy()
    reasons: list[str] = []

    if measures.content in ("raster", "mixed"):
        reasons.append(f"a {measures.content} sheet")
        below_floor = []
        if measures.dpi is not None and measures.dpi < policy.floor_dpi:
            below_floor.append(f"{measures.dpi:.0f} dpi, below {policy.floor_dpi:.0f}")
        if measures.ocr is not None and measures.ocr < policy.floor_ocr:
            below_floor.append(
                f"text read with {measures.ocr:.0%} confidence, below {policy.floor_ocr:.0%}"
            )
        if below_floor:
            reasons.extend(below_floor)
            return _verdict("low", True, reasons, policy)
        good = (measures.dpi is None or measures.dpi >= policy.good_dpi) and (
            measures.ocr is None or measures.ocr >= policy.good_ocr
        )
        if measures.dpi is not None:
            reasons.append(f"{measures.dpi:.0f} dpi")
        if measures.ocr is not None:
            reasons.append(f"text read with {measures.ocr:.0%} confidence")
        if good and measures.scale == "stated":
            return _verdict("medium", False, reasons, policy)
        if measures.scale != "stated":
            reasons.append(_scale_reason(measures.scale))
        # Readable, but either not sharp or not measurable: the estimator checks by hand.
        return _verdict("low" if not good else "medium", False, reasons, policy)

    if measures.content == "empty":
        return _verdict("low", False, ["the page is empty"], policy)

    reasons.append("a vector sheet")
    if measures.scale == "stated":
        return _verdict("high", False, reasons, policy)
    reasons.append(_scale_reason(measures.scale))
    return _verdict("medium", False, reasons, policy)


def _scale_reason(state: str) -> str:
    return (
        "marked not to scale, so lengths cannot be measured"
        if state == "not_to_scale"
        else ("no scale found, so lengths cannot be measured yet")
    )


def _verdict(band: str, manual: bool, reasons: list[str], policy: Policy) -> Verdict:
    return Verdict(band, manual, tuple(reasons), policy.bands.get(band, ""))


@lru_cache
def policies(path: Path = CONFIG) -> tuple[Policy, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    loaded = []
    for entry in raw["policies"]:
        raster = entry["raster"]
        effective = entry["effective_from"]
        loaded.append(
            Policy(
                effective_from=effective
                if isinstance(effective, date)
                else date.fromisoformat(str(effective)),
                good_dpi=float(raster["good_dpi"]),
                good_ocr=float(raster["good_ocr"]),
                floor_dpi=float(raster["floor_dpi"]),
                floor_ocr=float(raster["floor_ocr"]),
                bands={str(key): str(value) for key, value in entry["bands"].items()},
            )
        )
    if not loaded:
        raise ValueError("input_quality.yaml needs at least one policy")
    return tuple(sorted(loaded, key=lambda policy: policy.effective_from))


def current_policy(on: date | None = None) -> Policy:
    """The policy in force on a date: the latest whose `effective_from` has passed."""
    today = on or date.today()
    applicable = [policy for policy in policies() if policy.effective_from <= today]
    return applicable[-1] if applicable else policies()[0]

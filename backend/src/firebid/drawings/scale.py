"""A view's scale: what it states, what the drawing proves, and whether lengths are allowed.

FR-VIS-05: no length is produced from a view marked not to scale, or whose scale is
unverified or conflicting. So a stated scale is only a claim. It is **verified** when the
drawing's own dimensions agree with it: a DXF dimension entity (exact), or a dimension figure
on a PDF paired with the dimension line it sits on. A grid spacing that is dimensioned counts
the same way, which is how a scale is cross-checked against the grid.

Statuses:

* `verified`: stated, and every dimension that can be checked agrees within 1%.
* `unverified`: stated, but nothing on the drawing confirms it.
* `conflicting`: the dimensions imply a different scale than the one stated, or disagree.
* `nts`: marked not to scale. Never measured, whatever else it says.
* `calibrated`: a person measured two points of known distance (the calibration API).

Pure: geometry in, verdict and evidence out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from firebid.drawings.geometry import Kind, segments, texts

# Two scales within this relative difference are the same scale.
AGREEMENT = 0.01
# A dimension figure is between these (in drawing units, usually mm) to be read as one.
FIGURE = re.compile(r"^\s*(\d{2,6})(?:\s*mm)?\s*$", re.I)

STATED = re.compile(r"(?:^|\bSCALE\b\s*:?\s*)1\s*:\s*(\d{1,5})(?:\s*@\s*A\d)?", re.I)
NOT_TO_SCALE = re.compile(r"\bN\.?\s*T\.?\s*S\.?\b|NOT\s+TO\s+SCALE", re.I)


class ScaleStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    NTS = "nts"
    CALIBRATED = "calibrated"


MEASURABLE = frozenset({ScaleStatus.VERIFIED, ScaleStatus.CALIBRATED})


@dataclass(frozen=True)
class Stated:
    denominator: float | None
    nts: bool
    text: str | None = None


def parse_stated(text: str | None) -> Stated:
    """`1:100`, `SCALE 1:50 @ A1`, `NTS`, `NOT TO SCALE`; anything else states nothing."""
    if not text:
        return Stated(None, False)
    if NOT_TO_SCALE.search(text):
        return Stated(None, True, text.strip())
    match = STATED.search(text)
    if match and int(match.group(1)) > 0:
        return Stated(float(match.group(1)), False, text.strip())
    return Stated(None, False, text.strip())


@dataclass(frozen=True)
class Evidence:
    """One check: a dimension's stated length and the paper length it spans."""

    value: float  # the figure, in drawing units (mm)
    paper_mm: float  # its span on the sheet
    source: str  # "dimension" (a DXF entity) | "figure" (text paired with a line)
    at: tuple[float, float] = (0.0, 0.0)

    @property
    def denominator(self) -> float:
        return self.value / self.paper_mm


@dataclass(frozen=True)
class Verdict:
    status: ScaleStatus
    denominator: float | None  # the scale lengths are converted with, when measurable
    stated: Stated
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def measurable(self) -> bool:
        return self.status in MEASURABLE and self.denominator is not None

    def as_json(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "denominator": self.denominator,
            "stated": self.stated.denominator,
            "stated_text": self.stated.text,
            "nts": self.stated.nts,
            "reason": self.reason,
            "evidence": [
                {
                    "value": item.value,
                    "paper_mm": round(item.paper_mm, 4),
                    "implies": round(item.denominator, 3),
                    "source": item.source,
                    "at": [round(item.at[0], 2), round(item.at[1], 2)],
                }
                for item in self.evidence
            ],
        }


def same_scale(a: float, b: float) -> bool:
    largest = max(a, b)
    return largest > 0 and abs(a - b) / largest <= AGREEMENT


def verify(stated: Stated, evidence: list[Evidence]) -> Verdict:
    """Decide the status. Not to scale wins over everything; then the evidence decides."""
    if stated.nts:
        return Verdict(ScaleStatus.NTS, None, stated, tuple(evidence), "marked not to scale")
    if stated.denominator is None:
        if evidence and all(
            same_scale(item.denominator, evidence[0].denominator) for item in evidence
        ):
            return Verdict(
                ScaleStatus.UNVERIFIED,
                None,
                stated,
                tuple(evidence),
                "no scale is stated; the dimensions suggest 1:"
                f"{evidence[0].denominator:.0f}, which a person should confirm",
            )
        return Verdict(ScaleStatus.UNVERIFIED, None, stated, tuple(evidence), "no scale stated")
    if not evidence:
        return Verdict(
            ScaleStatus.UNVERIFIED,
            None,
            stated,
            (),
            f"1:{stated.denominator:.0f} is stated, but no dimension on the view confirms it",
        )
    disagreeing = [
        item for item in evidence if not same_scale(item.denominator, stated.denominator)
    ]
    if disagreeing:
        implied = ", ".join(f"1:{item.denominator:.0f}" for item in disagreeing[:3])
        return Verdict(
            ScaleStatus.CONFLICTING,
            None,
            stated,
            tuple(evidence),
            f"1:{stated.denominator:.0f} is stated but dimensions imply {implied}",
        )
    return Verdict(
        ScaleStatus.VERIFIED,
        stated.denominator,
        stated,
        tuple(evidence),
        f"{len(evidence)} dimension(s) agree with 1:{stated.denominator:.0f}",
    )


def in_box(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def evidence_in(table: pa.Table, extent: tuple[float, float, float, float]) -> list[Evidence]:
    """Every check the geometry inside a view offers: DXF dimensions, else PDF figures."""
    found = _dimension_entities(table, extent)
    return found if found else _paired_figures(table, extent)


def _dimension_entities(
    table: pa.Table, extent: tuple[float, float, float, float]
) -> list[Evidence]:
    dimensions = table.filter(pc.equal(table.column("kind"), pa.scalar(str(Kind.DIMENSION))))
    found = []
    for row in dimensions.select(["points", "value"]).to_pylist():
        x0, y0, x1, y1 = row["points"]
        paper = math.hypot(x1 - x0, y1 - y0)
        middle = ((x0 + x1) / 2, (y0 + y1) / 2)
        if row["value"] and paper > 0.5 and in_box(*middle, extent):
            found.append(Evidence(float(row["value"]), paper, "dimension", middle))
    return found


def _paired_figures(table: pa.Table, extent: tuple[float, float, float, float]) -> list[Evidence]:
    """A dimension figure on a PDF, paired with the dimension line it sits on.

    The figure sits along its line and just off it: within a quarter of the line's length of
    the line's midpoint, within a few text heights across it, and parallel to it. A pipe
    running past does not qualify, because its midpoint is somewhere else.
    """
    lines = segments(table)
    if len(lines) == 0:
        return []
    mid_x, mid_y = (lines.x0 + lines.x1) / 2, (lines.y0 + lines.y1) / 2
    lengths = lines.lengths
    angles = np.degrees(np.arctan2(lines.y1 - lines.y0, lines.x1 - lines.x0)) % 180
    cos, sin = np.abs(np.cos(np.radians(angles))), np.abs(np.sin(np.radians(angles)))
    # A line qualifies only if its midpoint is within three text heights of the figure, so
    # only those are looked at: found by their midpoint's x, which is sorted once. Every
    # line against every figure was most of reading a busy sheet's views (45 s of 47).
    by_x = np.argsort(mid_x, kind="stable")
    sorted_x = mid_x[by_x]
    found = []
    for span in texts(table):
        match = FIGURE.match(span["text"] or "")
        if not match:
            continue
        cx, cy = (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2
        if not in_box(cx, cy, extent):
            continue
        height = max(float(span["height"] or 0), span["maxy"] - span["miny"], 0.5)
        width = span["maxx"] - span["minx"]
        rotation = float(span["rotation"] or 0) % 180
        reach = 3 * height
        low = np.searchsorted(sorted_x, cx - reach, side="left")
        high = np.searchsorted(sorted_x, cx + reach, side="right")
        # In the lines' own order, so the nearest of equals is the one it always was.
        near = np.sort(by_x[low:high])
        if near.size == 0:
            continue
        along = cos[near] * np.abs(mid_x[near] - cx) + sin[near] * np.abs(mid_y[near] - cy)
        across = np.hypot(mid_x[near] - cx, mid_y[near] - cy)
        turned = np.abs(angles[near] - rotation)
        parallel = np.minimum(turned, 180 - turned) < 3
        candidates = np.flatnonzero(
            parallel & (lengths[near] > width) & (across < reach) & (along < 0.25 * lengths[near])
        )
        if candidates.size:
            best = near[candidates[np.argmin(across[candidates])]]
            value = float(match.group(1))
            # A figure of nought is a level or a count beside a line, not a length along it.
            if value > 0:
                found.append(Evidence(value, float(lengths[best]), "figure", (cx, cy)))
    return found


def measure(points: list[tuple[float, float]], verdict: Verdict) -> float:
    """A polyline's length in drawing units (mm). Refused unless the scale is measurable."""
    if not verdict.measurable or verdict.denominator is None:
        raise NotMeasurable(verdict)
    paper = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(points))
    return paper * verdict.denominator


class NotMeasurable(Exception):
    def __init__(self, verdict: Verdict) -> None:
        super().__init__(
            f"Lengths cannot be measured on this view: its scale is {verdict.status}"
            + (f" ({verdict.reason})" if verdict.reason else "")
            + ". Calibrate it from two points of known distance to measure."
        )
        self.verdict = verdict


def calibrated(
    points: tuple[tuple[float, float], tuple[float, float]], distance_mm: float, stated: Stated
) -> Verdict:
    """A person's calibration: two points and the real distance between them."""
    (x0, y0), (x1, y1) = points
    paper = math.hypot(x1 - x0, y1 - y0)
    if paper <= 0 or distance_mm <= 0:
        raise ValueError("calibration needs two different points and a positive distance")
    denominator = distance_mm / paper
    return Verdict(
        ScaleStatus.CALIBRATED,
        denominator,
        stated,
        (Evidence(distance_mm, paper, "calibration", ((x0 + x1) / 2, (y0 + y1) / 2)),),
        f"calibrated to 1:{denominator:.2f} by a person",
    )

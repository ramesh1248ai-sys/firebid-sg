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

A tender's upper floors are often drawn with no dimension at all, on the same grid as the
floors below. There the known dimension is the grid's: where a view whose scale is proved
shows gridlines AC and AD, how far apart they are is known, and a view that only states its
scale is checked against that (`known_spacings`, `corroborate`).

Pure: geometry in, verdict and evidence out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations, pairwise
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
    # "dimension" (a DXF entity) | "figure" (text paired with a line) | "grid" (two
    # gridlines whose spacing another view proves) | "calibration" (a person's)
    source: str
    at: tuple[float, float] = (0.0, 0.0)
    note: str = ""  # for a grid spacing: which gridlines, and which sheet says so

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
                | ({"note": item.note} if item.note else {})
                for item in self.evidence
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Verdict:
        """A stored verdict, as `as_json` wrote it."""
        denominator = data.get("denominator")
        stated = data.get("stated")
        return cls(
            status=ScaleStatus(str(data["status"])),
            denominator=None if denominator is None else float(denominator),
            stated=Stated(
                None if stated is None else float(stated),
                bool(data.get("nts")),
                data.get("stated_text"),
            ),
            evidence=tuple(
                Evidence(
                    float(item["value"]),
                    float(item["paper_mm"]),
                    str(item["source"]),
                    (float(item["at"][0]), float(item["at"][1])),
                    str(item.get("note", "")),
                )
                for item in data.get("evidence", [])
            ),
            reason=str(data.get("reason", "")),
        )


# A figure beside a line is taken for a dimension. On a real plan some are not: a grid
# bubble's number, a lot number, a figure beside the wrong line. Where at least this many
# dimensions agree with the stated scale and they are at least this share of all the
# evidence, the scale is verified and the rest are set aside, and said to be.
MAJORITY_AT_LEAST = 5
MAJORITY_SHARE = 0.8
# A length under this many millimetres is not dimensioned on a drawing of a building: a
# single figure beside a line is a label.
SMALLEST_DIMENSION_MM = 10.0


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
    agreeing = len(evidence) - len(disagreeing)
    if disagreeing and agreeing >= MAJORITY_AT_LEAST and agreeing >= MAJORITY_SHARE * len(evidence):
        return Verdict(
            ScaleStatus.VERIFIED,
            stated.denominator,
            stated,
            tuple(evidence),
            f"{agreeing} dimension(s) agree with 1:{stated.denominator:.0f}; "
            f"{len(disagreeing)} figure(s) beside a line do not and were set aside as not "
            "dimensions",
        )
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


# --- One view's scale checked against another's, by the grid they share --------------------

# A stated scale is taken as proved by the grid when at least this many spacings along one
# row of gridlines, each known from a view whose scale is proved, all agree with it.
GRID_SPACINGS_AT_LEAST = 3

# Rows of gridline marks across a sheet and up it (`grids.marks`).
Marks = dict[str, list[list[list[object]]]]


@dataclass(frozen=True)
class Spacing:
    """How far apart two named gridlines really are, and the sheet that proves it."""

    mm: float
    known_from: str


def _rows(marks: Marks) -> list[tuple[bool, list[tuple[str, float]]]]:
    """Each row of marks, and whether it runs across the sheet."""
    return [
        (axis == "across", [(str(label), float(str(position))) for label, position in row])
        for axis in ("across", "up")
        for row in marks.get(axis, [])
    ]


def known_spacings(proved: list[tuple[str, float, Marks]]) -> dict[tuple[str, str], Spacing]:
    """The real distance between each two gridlines that some proved view shows in one row.

    `proved` is each such view's sheet, its denominator and its grid marks. Two views that
    give one pair of gridlines different distances prove nothing about that pair: a tender
    of two buildings may name its gridlines alike, and the bubbles of a skewed grid stand
    wherever there was room for them.
    """
    known: dict[tuple[str, str], Spacing] = {}
    disputed: set[tuple[str, str]] = set()
    for name, denominator, marks in proved:
        for _, row in _rows(marks):
            for (first, at), (second, to) in combinations(row, 2):
                pair = (min(first, second), max(first, second))
                real = abs(to - at) * denominator
                if real <= 0 or pair in disputed:
                    continue
                if pair in known and not same_scale(known[pair].mm, real):
                    disputed.add(pair)
                    del known[pair]
                else:
                    known.setdefault(pair, Spacing(real, name))
    return known


def grid_evidence(marks: Marks, known: dict[tuple[str, str], Spacing]) -> list[list[Evidence]]:
    """A view's own gridlines as checks, a row at a time: each next two gridlines of a row
    whose real spacing is known."""
    found = []
    for across, row in _rows(marks):
        checks = []
        for (first, at), (second, to) in pairwise(row):
            spacing = known.get((min(first, second), max(first, second)))
            if spacing is None or to == at:
                continue
            middle = (at + to) / 2
            checks.append(
                Evidence(
                    spacing.mm,
                    abs(to - at),
                    "grid",
                    (middle, 0.0) if across else (0.0, middle),
                    f"gridlines {first} and {second} are {spacing.mm:.0f} mm apart on "
                    f"{spacing.known_from}",
                )
            )
        if checks:
            found.append(checks)
    return found


def corroborate(own: Verdict, grid: list[list[Evidence]]) -> Verdict | None:
    """What the grid makes of a view its own dimensions left unverified; None for no change.

    The view is verified where one row of its gridlines has enough known spacings and every
    one of them agrees with the stated scale: that cannot happen by chance to 1%. Spacings
    along another row that disagree are set aside and said to be. On a real tender they are
    a skewed wing's gridlines, whose bubbles do not stand square to them, so the grid never
    makes a view conflicting. A view that states no scale is verified only where its own
    dimensions, enough of them, and such a row all give the same one.
    """
    if own.status is not ScaleStatus.UNVERIFIED:
        return None
    scale_of = own.stated.denominator
    mine = sorted(item.denominator for item in own.evidence)
    if scale_of is None:
        if len(mine) < MAJORITY_AT_LEAST:
            return None
        scale_of = mine[len(mine) // 2]
        if not all(same_scale(item, scale_of) for item in mine):
            return None
        whole = float(round(scale_of))
        scale_of = whole if same_scale(whole, scale_of) else scale_of
    agreeing = [
        check
        for row in grid
        if len(row) >= GRID_SPACINGS_AT_LEAST
        and all(same_scale(check.denominator, scale_of) for check in row)
        for check in row
    ]
    if not agreeing:
        return None
    others = [check for row in grid for check in row if check not in agreeing]
    sheets = ", ".join(sorted({item.note.rsplit(" on ", 1)[-1] for item in agreeing})[:3])
    reason = (
        f"{len(agreeing)} grid spacing(s) known from {sheets} agree with 1:{scale_of:.0f}"
        if own.stated.denominator is not None
        else f"no scale is stated; {len(mine)} dimension(s) on the view and {len(agreeing)} "
        f"grid spacing(s) known from {sheets} all give 1:{scale_of:.0f}"
    )
    if others:
        reason += (
            f"; {len(others)} along another row of bubbles do not and were set aside (a "
            "skewed grid's bubbles are not square to it)"
        )
    return Verdict(
        ScaleStatus.VERIFIED, scale_of, own.stated, (*own.evidence, *agreeing, *others), reason
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
            # A figure of nought, or a single figure, is a level, a count or a label beside
            # a line, not a length along it.
            if value >= SMALLEST_DIMENSION_MM:
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

"""Symbol signatures: what makes two drawn symbols the same symbol (FR-VIS-02).

A symbol on a DXF is a block insert: its block name, and a hash of the block definition's
geometry (P1-03 records both on the insert). The hash is what matters: a renamed block with
the same drawing is the same symbol, and a reused name with a different drawing is not.

A symbol on a PDF is only line work. Small primitives that touch are clustered, and each
cluster gets a **shape descriptor** that does not change when the symbol is moved, turned or
uniformly scaled, so an instance on a plan matches its legend entry however it was placed:

* the line work is resampled at even spacing along its length (so a circle is the same
  whether a CAD circle or a PDF's Bézier curves drew it), about 200 points whatever the size;
* distances are divided by the points' RMS radius about their centroid (scale), and only
  distances are used (position and rotation);
* the descriptor is three normalised histograms: of every pairwise distance (the D2 shape
  distribution), of each point's distance from the centroid, and of how square the line
  runs to the centroid there.

Two descriptors are compared by half the L1 distance of each histogram, averaged: 0 for the
same shape, 1 for shapes with nothing in common. A DXF block's descriptor is computed the
same way from its exploded line work, which is how a PDF instance can match a mapping that
was confirmed on a DXF legend.

Pure: geometry in, clusters and signatures out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
import pyarrow as pa

from firebid.drawings.geometry import Kind, segments

# Bump when signatures change: stored signatures and instances are recomputed.
SIGNATURE_VERSION = "1"
# A symbol is at most this big on paper; anything larger is line work (pipes, walls, frames).
SYMBOL_MAX_MM = 15.0
# ...and at least this big, or it is a tick or a dot.
SYMBOL_MIN_MM = 1.0
# Primitives closer than this belong to one cluster.
TOUCH_MM = 0.3
SAMPLES = 200
D2_BINS = np.linspace(0.0, 3.2, 17)
RADIAL_BINS = np.linspace(0.0, 2.2, 12)
TURN_BINS = np.linspace(0.0, 1.0, 9)
# A CAD circle is walked as this many sides, near enough a PDF's four Beziers.
CIRCLE_SIDES = 64
# Two descriptors within this distance are the same symbol.
# Measured on the synthetic symbols: the same symbol, any placement, DXF or PDF, is within
# 0.025 of itself; the closest two different symbols (gate and check valve) are 0.154 apart.
DEFAULT_TOLERANCE = 0.07

SHAPE_KINDS = (Kind.LINE, Kind.POLYLINE, Kind.ARC, Kind.CIRCLE, Kind.HATCH)


@dataclass(frozen=True)
class Signature:
    """How a symbol is recognised. `descriptor` is always present; the block fields on DXF."""

    descriptor: tuple[float, ...]
    size_mm: float  # RMS radius on paper: the symbol's size, for scale estimates
    block: str | None = None
    block_hash: str | None = None
    tolerance: float = DEFAULT_TOLERANCE
    version: str = SIGNATURE_VERSION
    # How the line work is spread around the centre, by direction (drawing convention:
    # counter-clockwise, y up). Not part of matching: it is how orientation is found.
    angles: tuple[float, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "block": self.block,
            "block_hash": self.block_hash,
            "size_mm": round(self.size_mm, 4),
            "tolerance": self.tolerance,
            "descriptor": [round(value, 5) for value in self.descriptor],
            "angles": [round(value, 5) for value in self.angles],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Signature:
        return cls(
            descriptor=tuple(float(value) for value in data["descriptor"]),
            size_mm=float(data.get("size_mm") or 0.0),
            block=data.get("block"),
            block_hash=data.get("block_hash"),
            tolerance=float(data.get("tolerance") or DEFAULT_TOLERANCE),
            version=str(data.get("version") or SIGNATURE_VERSION),
            angles=tuple(float(value) for value in data.get("angles") or ()),
        )


@dataclass(frozen=True)
class Cluster:
    """One candidate symbol on a sheet: which primitives, where, and (DXF) which insert."""

    rows: tuple[int, ...]
    box: tuple[float, float, float, float]
    block: str | None = None
    block_hash: str | None = None
    rotation: float | None = None
    scale: float | None = None
    signature: Signature | None = field(default=None, compare=False)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


# --- The descriptor -------------------------------------------------------------------------


def sample(table: pa.Table, rows: tuple[int, ...] | list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Points evenly spaced along the line work of `rows`, and the line's direction at each.

    Spaced along the whole path at once, not per segment, so a circle drawn as 32 short
    segments is weighted exactly like a CAD circle of the same length.
    """
    picked = table.take(list(rows))
    lines = segments(picked, kinds=(Kind.LINE, Kind.POLYLINE, Kind.ARC, Kind.HATCH))
    starts = [np.column_stack([lines.x0, lines.y0])]
    ends = [np.column_stack([lines.x1, lines.y1])]
    for row in picked.select(["kind", "cx", "cy", "radius"]).to_pylist():
        if row["kind"] == str(Kind.CIRCLE) and row["radius"]:
            angle = np.linspace(0, 2 * math.pi, CIRCLE_SIDES + 1)
            xs = row["cx"] + row["radius"] * np.cos(angle)
            ys = row["cy"] + row["radius"] * np.sin(angle)
            starts.append(np.column_stack([xs[:-1], ys[:-1]]))
            ends.append(np.column_stack([xs[1:], ys[1:]]))
    first, last = np.vstack(starts), np.vstack(ends)
    lengths = np.hypot(*(last - first).T)
    keep = lengths > 0
    first, last, lengths = first[keep], last[keep], lengths[keep]
    total = float(lengths.sum())
    if total <= 0:
        return np.empty((0, 2)), np.empty((0, 2))
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    at = (np.arange(SAMPLES) + 0.5) * total / SAMPLES
    piece = np.clip(np.searchsorted(cumulative, at, side="right") - 1, 0, len(lengths) - 1)
    t = (at - cumulative[piece]) / lengths[piece]
    direction = (last - first)[piece] / lengths[piece][:, None]
    return first[piece] + direction * (t * lengths[piece])[:, None], direction


def describe(points: np.ndarray, directions: np.ndarray) -> tuple[tuple[float, ...], float] | None:
    """The descriptor and RMS radius of a point set; None when there is nothing to describe.

    Three histograms, each invariant to position, rotation and uniform scale: pairwise
    distances (D2), distance from the centroid, and how square the line runs to the
    centroid (|cos| of the angle between line and radius), which tells a bow-tie from a
    triangle that D2 alone finds alike.
    """
    if len(points) < 8:
        return None
    centred = points - points.mean(axis=0)
    radii = np.hypot(centred[:, 0], centred[:, 1])
    rms = float(np.sqrt(np.mean(radii**2)))
    if rms <= 1e-9:
        return None
    difference = centred[:, None, :] - centred[None, :, :]
    pairwise = np.hypot(difference[..., 0], difference[..., 1])[np.triu_indices(len(points), 1)]
    d2, _ = np.histogram(pairwise / rms, bins=D2_BINS)
    radial, _ = np.histogram(radii / rms, bins=RADIAL_BINS)
    outward = centred / np.maximum(radii, 1e-9)[:, None]
    along = np.abs((outward * directions).sum(axis=1))
    turn, _ = np.histogram(along[radii > 1e-6 * rms], bins=TURN_BINS)
    parts = [d2, radial, turn]
    descriptor = np.concatenate([part / max(part.sum(), 1) for part in parts])
    return tuple(float(value) for value in descriptor), rms


def distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """0 for the same shape, up to 1 for shapes with nothing in common."""
    first, second = np.asarray(a), np.asarray(b)
    if first.shape != second.shape:
        return 1.0
    bounds = np.cumsum([0, len(D2_BINS) - 1, len(RADIAL_BINS) - 1, len(TURN_BINS) - 1])
    gaps = [
        0.5 * np.abs(first[low:high] - second[low:high]).sum() for low, high in pairwise(bounds)
    ]
    return float(np.mean(gaps))


ANGLE_BINS = 72  # 5 degrees each


def angular_profile(points: np.ndarray) -> tuple[float, ...]:
    """The share of the line work in each direction from the centre, y up (drawing)."""
    if len(points) == 0:
        return ()
    centred = points - points.mean(axis=0)
    # Sheet y points down; drawings turn counter-clockwise with y up.
    angle = np.degrees(np.arctan2(-centred[:, 1], centred[:, 0])) % 360.0
    weights = np.hypot(centred[:, 0], centred[:, 1])
    counts, _ = np.histogram(angle, bins=ANGLE_BINS, range=(0.0, 360.0), weights=weights)
    total = counts.sum() or 1.0
    return tuple(float(value) for value in counts / total)


def _smooth(profile: np.ndarray, width: int = 2) -> np.ndarray:
    """A circular blur over a few bins, so a line lying on a bin edge counts the same
    whichever side of it sampling puts it: without it a symmetric symbol matches itself
    more sharply than its turned copies, and reports a turn it does not have."""
    kernel = np.exp(-0.5 * (np.arange(-2 * width, 2 * width + 1) / width) ** 2)
    padded = np.concatenate([profile[-2 * width :], profile, profile[: 2 * width]])
    return np.convolve(padded, kernel / kernel.sum(), mode="valid")


def orientation(instance: Signature, reference: Signature, margin: float = 0.08) -> float | None:
    """How far the instance is turned from its legend entry, in degrees, when that is clear.

    The turn is the one that best lines up the two angular profiles. A symmetric symbol
    (a circle with a cross) lines up equally well several ways, so it has no orientation to
    report: None unless the best turn beats every turn more than 30 degrees from it.
    """
    if not instance.angles or not reference.angles:
        return None
    a, b = _smooth(np.asarray(instance.angles)), _smooth(np.asarray(reference.angles))
    scores = np.asarray([float(np.dot(a, np.roll(b, shift))) for shift in range(ANGLE_BINS)])
    best = int(np.argmax(scores))
    distance = np.minimum(
        np.abs(np.arange(ANGLE_BINS) - best), ANGLE_BINS - np.abs(np.arange(ANGLE_BINS) - best)
    )
    others = scores[distance > 30 / (360 / ANGLE_BINS)]
    if others.size and scores[best] - others.max() < margin * scores[best]:
        return None
    return float(best * 360 / ANGLE_BINS)


def signature_of(table: pa.Table, cluster: Cluster) -> Signature | None:
    points, directions = sample(table, cluster.rows)
    described = describe(points, directions)
    if described is None:
        return None
    descriptor, rms = described
    return Signature(
        descriptor, rms, cluster.block, cluster.block_hash, angles=angular_profile(points)
    )


# --- Finding candidate symbols --------------------------------------------------------------


def clusters(
    table: pa.Table,
    *,
    within: tuple[float, float, float, float] | None = None,
    excluding: list[tuple[float, float, float, float]] | None = None,
    signed: bool = True,
) -> list[Cluster]:
    """Every candidate symbol on the sheet, each with its signature.

    A DXF insert is one symbol, made of the primitives exploded from it. Everything else is
    clustered: small primitives whose boxes touch. Text is never part of a symbol's shape.
    `within` keeps only clusters centred in that box; `excluding` drops those centred in any
    of these (a legend is not a place where objects are installed). `signed=False` leaves the
    signatures off, which are nearly all the cost, for a caller that needs only where the
    shapes are.
    """
    columns = table.select(
        [
            "kind",
            "group",
            "block",
            "text",
            "value",
            "rotation",
            "layer",
            "color",
            "minx",
            "miny",
            "maxx",
            "maxy",
        ]
    ).to_pydict()
    kinds = columns["kind"]
    shape = {str(kind) for kind in SHAPE_KINDS}
    found: list[Cluster] = []

    in_insert: set[int] = set()
    by_group: dict[int, list[int]] = {}
    for row, kind in enumerate(kinds):
        if kind in shape:
            by_group.setdefault(columns["group"][row], []).append(row)
    for row, kind in enumerate(kinds):
        if kind != str(Kind.INSERT):
            continue
        parts = tuple(by_group.get(columns["group"][row], []))
        if not parts:
            continue
        in_insert.update(parts)
        box = _box_of(columns, parts)
        if not _symbol_sized(box):
            continue
        found.append(
            Cluster(
                rows=parts,
                box=box,
                block=columns["block"][row],
                block_hash=columns["text"][row],
                rotation=columns["rotation"][row],
                scale=columns["value"][row],
            )
        )

    loose = [
        row
        for row, kind in enumerate(kinds)
        if kind in shape
        and row not in in_insert
        and columns["minx"][row] is not None
        and _symbol_sized(_box_of(columns, (row,)))
    ]
    for group in _touching(columns, loose):
        box = _box_of(columns, group)
        if _symbol_sized(box):
            found.append(Cluster(rows=tuple(group), box=box))

    kept = []
    for cluster in found:
        cx, cy = cluster.centre
        if within is not None and not _inside(cx, cy, within):
            continue
        if excluding and any(_inside(cx, cy, box) for box in excluding):
            continue
        if not signed:
            kept.append(cluster)
            continue
        signature = signature_of(table, cluster)
        if signature is not None:
            kept.append(_with(cluster, signature))
    return kept


def _with(cluster: Cluster, signature: Signature) -> Cluster:
    return Cluster(
        cluster.rows,
        cluster.box,
        cluster.block,
        cluster.block_hash,
        cluster.rotation,
        cluster.scale,
        signature,
    )


def _inside(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _box_of(
    columns: dict[str, list[Any]], rows: tuple[int, ...] | list[int]
) -> tuple[float, float, float, float]:
    return (
        min(columns["minx"][row] for row in rows),
        min(columns["miny"][row] for row in rows),
        max(columns["maxx"][row] for row in rows),
        max(columns["maxy"][row] for row in rows),
    )


def _symbol_sized(box: tuple[float, float, float, float]) -> bool:
    diagonal = math.hypot(box[2] - box[0], box[3] - box[1])
    return SYMBOL_MIN_MM <= diagonal <= SYMBOL_MAX_MM * math.sqrt(2)


def _touching(columns: dict[str, list[Any]], rows: list[int]) -> list[list[int]]:
    """Groups of primitives whose boxes touch, by union-find over a sort-and-sweep.

    Only primitives drawn alike (same layer and colour) join: a symbol is drawn in one pen,
    so the pipe running into a valve's body stays pipe rather than becoming part of it.
    """
    parent = {row: row for row in rows}

    def root(row: int) -> int:
        while parent[row] != row:
            parent[row] = parent[parent[row]]
            row = parent[row]
        return row

    ordered = sorted(rows, key=lambda row: columns["minx"][row])
    active: list[int] = []
    for row in ordered:
        left = columns["minx"][row] - TOUCH_MM
        active = [other for other in active if columns["maxx"][other] >= left]
        for other in active:
            if (
                columns["layer"][other] == columns["layer"][row]
                and columns["color"][other] == columns["color"][row]
                and columns["miny"][other] <= columns["maxy"][row] + TOUCH_MM
                and columns["maxy"][other] >= columns["miny"][row] - TOUCH_MM
            ):
                parent[root(other)] = root(row)
        active.append(row)
    groups: dict[int, list[int]] = {}
    for row in rows:
        groups.setdefault(root(row), []).append(row)
    return list(groups.values())


# --- Matching -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    index: int
    distance: float
    how: str  # block_hash | block | shape


WIDTH = (len(D2_BINS) - 1) + (len(RADIAL_BINS) - 1) + (len(TURN_BINS) - 1)
_PART_STARTS = np.array([0, len(D2_BINS) - 1, len(D2_BINS) + len(RADIAL_BINS) - 2])


class Candidates:
    """Signatures to match against, their descriptors stacked once.

    A sheet has thousands of instances to match against the same legend rows or mappings:
    comparing one against all of them as arrays, not pair by pair, is what keeps symbol
    reading to seconds a sheet (P1-11). Grows by `append`, for grouping unknown symbols.
    """

    def __init__(self, signatures: list[Signature] | tuple[Signature, ...] = ()) -> None:
        self.signatures: list[Signature] = []
        self._rows = np.zeros((max(len(signatures), 16), WIDTH))
        self._tolerances = np.zeros(self._rows.shape[0])
        self._hashes: dict[str, int] = {}
        # Candidates with no descriptor never match by shape; ones of another width (an
        # older signature version) are compared one by one, and are always far.
        self._odd: list[int] = []
        for signature in signatures:
            self.append(signature)

    def __len__(self) -> int:
        return len(self.signatures)

    def __getitem__(self, index: int) -> Signature:
        return self.signatures[index]

    def append(self, signature: Signature) -> None:
        index = len(self.signatures)
        self.signatures.append(signature)
        if signature.block_hash and signature.block_hash not in self._hashes:
            self._hashes[signature.block_hash] = index
        if index == self._rows.shape[0]:
            self._rows = np.vstack([self._rows, np.zeros_like(self._rows)])
            self._tolerances = np.concatenate([self._tolerances, np.zeros_like(self._tolerances)])
        self._tolerances[index] = signature.tolerance
        if len(signature.descriptor) == WIDTH:
            self._rows[index] = signature.descriptor
        else:
            self._rows[index] = np.nan
            if signature.descriptor:
                self._odd.append(index)

    def by_hash(self, block_hash: str) -> int | None:
        return self._hashes.get(block_hash)

    def gaps(self, signature: Signature) -> tuple[np.ndarray, np.ndarray]:
        """Each candidate's `distance` from `signature` (NaN: no descriptor), and the
        tolerance each pair is held to."""
        count = len(self.signatures)
        tolerances = np.minimum(self._tolerances[:count], signature.tolerance)
        rows = self._rows[:count]
        if len(signature.descriptor) == WIDTH:
            halves = 0.5 * np.abs(rows - np.asarray(signature.descriptor))
            found = np.add.reduceat(halves, _PART_STARTS, axis=1).mean(axis=1)
        else:
            found = np.where(np.isnan(rows[:, 0]), np.nan, 1.0)
        for index in self._odd:
            found[index] = distance(signature.descriptor, self.signatures[index].descriptor)
        return found, tolerances


def _as_candidates(candidates: Candidates | list[Signature]) -> Candidates:
    return candidates if isinstance(candidates, Candidates) else Candidates(candidates)


def best_match(signature: Signature, candidates: Candidates | list[Signature]) -> Match | None:
    """The candidate this signature is, if any: same block geometry first, then same shape.

    A block name alone is not enough: consultants reuse names. A name with a different hash
    falls through to the shape comparison like any PDF symbol. Pass `Candidates` when
    matching many signatures against the same list.
    """
    stacked = _as_candidates(candidates)
    if len(stacked) == 0:
        return None
    if signature.block_hash:
        index = stacked.by_hash(signature.block_hash)
        if index is not None:
            return Match(index, 0.0, "block_hash")
    gaps, tolerances = stacked.gaps(signature)
    within = np.flatnonzero(gaps <= tolerances)
    if within.size == 0:
        return None
    index = int(within[np.argmin(gaps[within])])
    return Match(index, float(gaps[index]), "shape")


def near_match(
    signature: Signature, candidates: Candidates | list[Signature], factor: float
) -> Match | None:
    """The closest candidate a shape is *nearly* like: outside its tolerance, within
    `factor` times it. What vision assist may be asked about; never a match by itself."""
    stacked = _as_candidates(candidates)
    if len(stacked) == 0:
        return None
    gaps, tolerances = stacked.gaps(signature)
    near = np.flatnonzero((gaps > tolerances) & (gaps <= factor * tolerances))
    if near.size == 0:
        return None
    index = int(near[np.argmin(gaps[near])])
    return Match(index, float(gaps[index]), "near")

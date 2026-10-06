"""A sheet's geometry, normalised: every source becomes the same primitives (FR-VIS-01).

One model for DXF and PDF, so symbol matching, pipe tracing and measurement are written once.
Coordinates are **sheet millimetres, y down**, the frame the title block reader and the
viewer already use. Model coordinates are sheet coordinates times a verified scale, and only
the scale module turns one into the other.

Primitives:

* `line`: two points.
* `polyline`: points in order, open or closed. PDF paths become polylines, Béziers
  flattened, and a PDF circle is four Béziers, so it arrives here as a closed polyline.
* `arc`, `circle`: centre, radius and angles, where the source says so exactly (DXF).
* `text`: a span with its box, height and rotation.
* `insert`: a block reference, kept as itself as well as exploded, because P1-04 matches
  symbols on inserts.
* `hatch`: a hatch boundary, as a closed polyline.

Every primitive records how it was extracted and which group it came from. A group is a PDF
path object or a DXF block insert; symbol structure lives in that grouping, so it is kept.

Storage is columnar (Parquet), built once per sheet and read many times, and the hot paths
that query it work on NumPy arrays and Shapely's STRtree rather than per-primitive Python.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

# Bump when extraction changes what it produces: the stage cache is keyed on it, so every
# sheet is re-extracted once, and never again until the next bump.
# 2: inserts carry their block's geometry hash (`text`) and scale (`value`).
EXTRACTOR_VERSION = "2"

# How finely a Bézier is flattened. A 5 mm sprinkler circle stays round at eight per curve.
BEZIER_STEPS = 8


class Kind(StrEnum):
    LINE = "line"
    POLYLINE = "polyline"
    ARC = "arc"
    CIRCLE = "circle"
    TEXT = "text"
    INSERT = "insert"
    HATCH = "hatch"
    # A dimension: its two measured points and the value it states, in drawing units. The
    # evidence a scale is verified on (FR-VIS-05).
    DIMENSION = "dimension"


class Method(StrEnum):
    """How a primitive was obtained (FR-VIS-01: recorded for every one)."""

    CAD = "cad_entity"  # a DXF entity, exact
    PDF_VECTOR = "pdf_vector"  # a PDF path or text object, through PDFium
    PDF_FALLBACK = "pdf_fallback"  # the same, through pdfplumber when PDFium could not
    OCR = "ocr"  # text read from pixels


_FIELDS: list[tuple[str, pa.DataType]] = [
    ("kind", pa.string()),
    ("method", pa.string()),
    ("group", pa.int32()),
    ("layer", pa.string()),
    ("color", pa.int32()),  # 0xRRGGBB, -1 when the source gives none
    ("linetype", pa.string()),
    ("lineweight", pa.float32()),  # mm
    ("closed", pa.bool_()),
    ("points", pa.list_(pa.float64())),  # x0, y0, x1, y1, ... in sheet mm
    ("cx", pa.float64()),
    ("cy", pa.float64()),
    ("radius", pa.float64()),
    ("start_angle", pa.float64()),  # degrees, counter-clockwise from +x, y up
    ("end_angle", pa.float64()),
    ("text", pa.string()),
    ("height", pa.float64()),
    ("rotation", pa.float64()),
    ("confidence", pa.float32()),
    ("block", pa.string()),
    ("value", pa.float64()),  # a dimension's stated measurement
    ("minx", pa.float64()),
    ("miny", pa.float64()),
    ("maxx", pa.float64()),
    ("maxy", pa.float64()),
]
SCHEMA = pa.schema([pa.field(name, kind) for name, kind in _FIELDS])


# Primitives are packed away this many at a time as they are collected. A real A0 plan has
# two million of them, and held as Python values until the end they took over a gigabyte,
# which with the table made from them was more than a parser job is allowed.
PACK_EVERY = 50_000


@dataclass
class Builder:
    """Collects primitives during extraction, then freezes them into a table.

    Extraction is a per-object walk whatever the engine, so this is the one place a Python
    loop over primitives is expected. Everything downstream works on the columns.

    The primitives collected are packed into the table's own form every `PACK_EVERY`, so
    the memory in use is the table so far and one batch of Python values, not all of them.
    """

    method: Method
    # The primitives not yet packed. `len(builder)` and `has(kind)` answer for all of them.
    columns: dict[str, list[Any]] = field(
        default_factory=lambda: {name: [] for name in SCHEMA.names}
    )
    next_group: int = 0
    _packed: list[pa.RecordBatch] = field(default_factory=list, repr=False)
    _packed_rows: int = 0
    _kinds: set[str] = field(default_factory=set, repr=False)
    _held: bool = field(default=False, repr=False)

    def __len__(self) -> int:
        return self._packed_rows + len(self.columns["kind"])

    def has(self, kind: Kind) -> bool:
        """Whether any primitive of this kind has been collected."""
        return str(kind) in self._kinds

    @contextmanager
    def unpacked(self) -> Iterator[int]:
        """Collect without packing for a while, for a caller that goes back over what it
        has just added (a DXF viewport, which cuts its lines at its edge). Yields where in
        `columns` its own primitives start."""
        self._pack()
        self._held = True
        try:
            yield len(self.columns["kind"])
        finally:
            self._held = False
            self._pack()

    def _pack(self) -> None:
        rows = len(self.columns["kind"])
        if not rows:
            return
        self._packed.append(pa.record_batch(self.columns, schema=SCHEMA))
        self._packed_rows += rows
        self.columns = {name: [] for name in SCHEMA.names}

    def group(self) -> int:
        self.next_group += 1
        return self.next_group

    def _add(self, kind: Kind, points: list[float], group: int, **values: Any) -> None:
        xs, ys = points[0::2], points[1::2]
        row = {
            "kind": str(kind),
            "method": str(values.pop("method", self.method)),
            "group": group,
            "layer": values.pop("layer", None),
            "color": values.pop("color", -1),
            "linetype": values.pop("linetype", None),
            "lineweight": values.pop("lineweight", None),
            "closed": values.pop("closed", False),
            "points": points,
            "cx": values.pop("cx", None),
            "cy": values.pop("cy", None),
            "radius": values.pop("radius", None),
            "start_angle": values.pop("start_angle", None),
            "end_angle": values.pop("end_angle", None),
            "text": values.pop("text", None),
            "height": values.pop("height", None),
            "rotation": values.pop("rotation", None),
            "confidence": values.pop("confidence", None),
            "block": values.pop("block", None),
            "value": values.pop("value", None),
            "minx": values.pop("minx", min(xs) if xs else None),
            "miny": values.pop("miny", min(ys) if ys else None),
            "maxx": values.pop("maxx", max(xs) if xs else None),
            "maxy": values.pop("maxy", max(ys) if ys else None),
        }
        if values:
            raise TypeError(f"unknown primitive fields: {sorted(values)}")
        for name, value in row.items():
            self.columns[name].append(value)
        self._kinds.add(row["kind"])
        if not self._held and len(self.columns["kind"]) >= PACK_EVERY:
            self._pack()

    def line(self, x0: float, y0: float, x1: float, y1: float, group: int, **style: Any) -> None:
        self._add(Kind.LINE, [x0, y0, x1, y1], group, **style)

    def polyline(
        self, points: list[float], group: int, *, closed: bool = False, **style: Any
    ) -> None:
        if len(points) < 4:
            return
        self._add(Kind.POLYLINE, points, group, closed=closed, **style)

    def circle(self, cx: float, cy: float, radius: float, group: int, **style: Any) -> None:
        self._add(
            Kind.CIRCLE,
            [],
            group,
            cx=cx,
            cy=cy,
            radius=radius,
            minx=cx - radius,
            miny=cy - radius,
            maxx=cx + radius,
            maxy=cy + radius,
            **style,
        )

    def arc(
        self,
        cx: float,
        cy: float,
        radius: float,
        start: float,
        end: float,
        group: int,
        **style: Any,
    ) -> None:
        points = arc_points(cx, cy, radius, start, end)
        self._add(
            Kind.ARC,
            points,
            group,
            cx=cx,
            cy=cy,
            radius=radius,
            start_angle=start,
            end_angle=end,
            **style,
        )

    def text(
        self,
        text: str,
        box: tuple[float, float, float, float],
        group: int,
        *,
        height: float,
        rotation: float = 0.0,
        confidence: float | None = None,
        **style: Any,
    ) -> None:
        x0, y0, x1, y1 = box
        self._add(
            Kind.TEXT,
            [],
            group,
            text=text,
            height=height,
            rotation=rotation,
            confidence=confidence,
            minx=x0,
            miny=y0,
            maxx=x1,
            maxy=y1,
            **style,
        )

    def insert(
        self,
        block: str,
        x: float,
        y: float,
        group: int,
        *,
        box: tuple[float, float, float, float],
        rotation: float = 0.0,
        **style: Any,
    ) -> None:
        self._add(
            Kind.INSERT,
            [x, y],
            group,
            block=block,
            rotation=rotation,
            minx=box[0],
            miny=box[1],
            maxx=box[2],
            maxy=box[3],
            **style,
        )

    def dimension(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        group: int,
        *,
        value: float,
        text: str,
        **style: Any,
    ) -> None:
        self._add(Kind.DIMENSION, [x0, y0, x1, y1], group, value=value, text=text, **style)

    def hatch(self, points: list[float], group: int, **style: Any) -> None:
        if len(points) >= 6:
            self._add(Kind.HATCH, points, group, closed=True, **style)

    def table(self) -> pa.Table:
        self._pack()
        return pa.Table.from_batches(self._packed, schema=SCHEMA)


def arc_points(
    cx: float, cy: float, radius: float, start: float, end: float, segments_per_circle: int = 64
) -> list[float]:
    """An arc as points, in sheet coordinates (y down), from angles measured with y up."""
    sweep = (end - start) % 360 or 360.0
    steps = max(2, math.ceil(segments_per_circle * sweep / 360))
    points: list[float] = []
    for step in range(steps + 1):
        angle = math.radians(start + sweep * step / steps)
        points += [cx + radius * math.cos(angle), cy - radius * math.sin(angle)]
    return points


def bezier_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = BEZIER_STEPS,
) -> list[float]:
    """A cubic Bézier flattened to points, excluding the start (already emitted)."""
    t = np.linspace(0.0, 1.0, steps + 1)[1:]
    a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
    xs = a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0]
    ys = a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]
    return np.column_stack([xs, ys]).ravel().tolist()


# --- Storage --------------------------------------------------------------------------------


def to_parquet(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def from_parquet(payload: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(payload))


def counts(table: pa.Table) -> dict[str, int]:
    counted: Any = pc.value_counts(table.column("kind"))
    found = {str(item["values"]): int(item["counts"]) for item in counted.to_pylist()}
    return {str(kind): found[str(kind)] for kind in Kind if str(kind) in found}


# A real A0 plan has two million primitives. A column read as a Python value a primitive is
# tens of bytes each, a hundred megabytes a column; these read a column without doing that.


def kind_mask(table: pa.Table, kinds: tuple[Kind, ...]) -> np.ndarray:
    """Which rows are of these kinds, as a boolean array."""
    wanted = pa.array([str(kind) for kind in kinds], type=table.schema.field("kind").type)
    found: Any = pc.is_in(table.column("kind"), value_set=wanted)
    mask: np.ndarray = found.to_numpy(zero_copy_only=False).astype(bool)
    return mask


def shared_values(column: Any) -> np.ndarray:
    """A column of words (layers, kinds, block names) as an array of Python values in
    which equal words are one object: a pointer a row, however many rows there are."""
    whole = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    if len(whole) == 0:
        return np.empty(0, dtype=object)
    encoded = whole.dictionary_encode()
    words = np.empty(len(encoded.dictionary) + 1, dtype=object)
    words[:-1] = encoded.dictionary.to_pylist()
    words[-1] = None
    at = encoded.indices.fill_null(len(encoded.dictionary)).to_numpy(zero_copy_only=False)
    shared: np.ndarray = words[at]
    return shared


def numbers(column: Any) -> np.ndarray:
    """A column of numbers as floats, with NaN where it has none."""
    whole = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    values: np.ndarray = pc.cast(whole, pa.float64()).to_numpy(zero_copy_only=False)
    return values


# --- Reading it back, vectorised -------------------------------------------------------------


@dataclass(frozen=True)
class Segments:
    """Every straight piece of line work as four arrays: the form measurement works on."""

    x0: np.ndarray
    y0: np.ndarray
    x1: np.ndarray
    y1: np.ndarray
    layer: np.ndarray
    row: np.ndarray  # which primitive each segment came from
    # The segments are in primitive order up to here; from here on are the ones that close
    # a closed polyline (its last point to its first), again in primitive order.
    closing_from: int = -1

    @property
    def lengths(self) -> np.ndarray:
        return cast(np.ndarray, np.hypot(self.x1 - self.x0, self.y1 - self.y0))

    def __len__(self) -> int:
        return int(self.x0.size)


def segments(
    table: pa.Table, kinds: tuple[Kind, ...] = (Kind.LINE, Kind.POLYLINE, Kind.ARC)
) -> Segments:
    """Explode lines, polylines and arcs into segments, without a Python loop per point."""
    rows = np.flatnonzero(kind_mask(table, kinds))
    if rows.size == 0:
        empty = np.empty(0)
        return Segments(
            empty, empty, empty, empty, np.empty(0, dtype=object), np.empty(0, dtype=np.int64)
        )
    # Only the columns read below: a copy of every column of two million rows is a few
    # hundred megabytes that nothing looks at.
    picked = table.select(["points", "closed", "layer"]).take(rows)
    points = cast("pa.ListArray[Any]", picked.column("points").combine_chunks())
    flat = points.values.to_numpy(zero_copy_only=False)
    offsets = points.offsets.to_numpy()
    closed = picked.column("closed").to_numpy(zero_copy_only=False)
    layers = shared_values(picked.column("layer"))

    # Point i of the flat array starts a segment unless it is the last of its primitive.
    point_offsets = offsets // 2
    counts_per_row = np.diff(point_offsets)
    xs, ys = flat[0::2], flat[1::2]
    starts = np.ones(xs.size, dtype=bool)
    starts[point_offsets[1:] - 1] = False
    index = np.flatnonzero(starts)
    owner = np.repeat(np.arange(rows.size), counts_per_row)
    seg_x0, seg_y0 = xs[index], ys[index]
    seg_x1, seg_y1 = xs[index + 1], ys[index + 1]
    seg_owner = owner[index]

    # A closed polyline also joins its last point to its first.
    closing = np.flatnonzero(closed & (counts_per_row > 2))
    closing_from = int(seg_x0.size)
    if closing.size:
        first = point_offsets[closing]
        last = point_offsets[closing + 1] - 1
        seg_x0 = np.concatenate([seg_x0, xs[last]])
        seg_y0 = np.concatenate([seg_y0, ys[last]])
        seg_x1 = np.concatenate([seg_x1, xs[first]])
        seg_y1 = np.concatenate([seg_y1, ys[first]])
        seg_owner = np.concatenate([seg_owner, closing])

    return Segments(
        seg_x0, seg_y0, seg_x1, seg_y1, layers[seg_owner], rows[seg_owner], closing_from
    )


def texts(table: pa.Table) -> list[dict[str, Any]]:
    """The text spans, as rows. Few enough per sheet that a list is the right shape."""
    rows = np.flatnonzero(kind_mask(table, (Kind.TEXT,)))
    picked = table.take(rows).select(
        [
            "text",
            "minx",
            "miny",
            "maxx",
            "maxy",
            "height",
            "rotation",
            "method",
            "confidence",
            "layer",
        ]
    )
    return picked.to_pylist()

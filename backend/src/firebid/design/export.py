"""A sheet's proposed layout over its tender drawing, as a PDF or a DXF (FR-DSN-05).

The tender drawing is drawn from the sheet's extracted geometry, in grey, so the file shows
exactly what the platform read and can be made for a PDF sheet and a DXF sheet alike. Over
it, in colour: the proposed heads, the proposed range pipes and feeds with their sizes, and
the part of the plan the sheet answers for where match lines limit it.

Every export is stamped "For estimation only: not for construction", across the sheet and
in a note that says what the layout rests on. A proposed layout is an estimating aid, never
a design for the Qualified Person's approval (requirements §2.1).

Pure: a sheet's geometry and layout in, a file's bytes out. Sheet millimetres, y down.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pyarrow as pa

from firebid.drawings import geometry
from firebid.drawings.geometry import Kind

STAMP = "For estimation only: not for construction"
FORMATS = {"pdf": "application/pdf", "dxf": "application/dxf"}

Box = tuple[float, float, float, float]
Point = tuple[float, float]

HEAD_RADIUS_MM = 1.2
LAYER_TENDER = "TENDER-DRAWING"
LAYER_HEADS = "PROPOSED-HEADS"
LAYER_PIPE = "PROPOSED-PIPE"
LAYER_SCOPE = "PROPOSED-SCOPE"
LAYER_STAMP = "ESTIMATION-ONLY"


@dataclass(frozen=True)
class Head:
    x: float
    y: float
    object_type: str


@dataclass(frozen=True)
class Pipe:
    points: list[Point]
    dn: int | None
    part: str  # range | feed


@dataclass
class Sheet:
    """What an export shows: the sheet as read, its proposed layout, and what it rests on."""

    number: str
    page: Box
    table: pa.Table
    heads: list[Head]
    pipes: list[Pipe]
    scope: list[Point] | None = None
    # The note under the stamp: criterion, rule version, counts, who confirmed, when made.
    notes: list[str] = field(default_factory=list)


def _note_lines(sheet: Sheet) -> list[str]:
    return [
        STAMP.upper(),
        f"Proposed sprinkler layout on {sheet.number}: an estimating aid, not a design.",
        *sheet.notes,
    ]


def _tender_lines(table: pa.Table) -> list[np.ndarray]:
    """The tender drawing's linework, a run of points a primitive: (n, 2) arrays, sheet mm.

    A real sheet has over a million straight pieces in a few hundred thousand lines and
    polylines. Drawn a primitive at a time, a file is a third the size and made in a third
    of the time it takes a piece at a time.
    """
    kinds = np.asarray(table.column("kind").to_pylist(), dtype=object)
    rows = np.flatnonzero(np.isin(kinds, [str(Kind.LINE), str(Kind.POLYLINE)]))
    out: list[np.ndarray] = []
    if rows.size:
        picked = table.take(rows)
        points = cast("pa.ListArray[Any]", picked.column("points").combine_chunks())
        flat = points.values.to_numpy(zero_copy_only=False).reshape(-1, 2)
        offsets = points.offsets.to_numpy() // 2
        closed = picked.column("closed").to_numpy(zero_copy_only=False)
        for run, shut in zip(np.split(flat, offsets[1:-1]), closed, strict=True):
            if len(run) < 2:
                continue
            out.append(np.vstack([run, run[:1]]) if shut and len(run) > 2 else run)
    arcs = geometry.segments(table, kinds=(Kind.ARC,))
    if len(arcs):
        pieces = np.stack(
            [np.column_stack([arcs.x0, arcs.y0]), np.column_stack([arcs.x1, arcs.y1])], axis=1
        )
        out.extend(pieces)
    return out


def _tender_circles(table: pa.Table) -> list[tuple[float, float, float]]:
    return [
        (float(row["cx"]), float(row["cy"]), float(row["radius"]))
        for row in table.select(["kind", "cx", "cy", "radius"]).to_pylist()
        if row["kind"] == str(Kind.CIRCLE) and row["radius"]
    ]


def _written_from(span: dict[str, Any]) -> tuple[float, float, float]:
    """Where a span's words start, and the way they run: (x, y, degrees), sheet mm, y down.

    The geometry keeps a span's box and its rotation. Words written up the sheet (90) start
    at the bottom of their box with their feet to the right; turned over (180) at its far
    corner; down the sheet (270) at its top with their feet to the left.
    """
    rotation = float(span.get("rotation") or 0.0) % 360.0
    left, top = float(span["minx"]), float(span["miny"])
    right, bottom = float(span["maxx"]), float(span["maxy"])
    quarter = round(rotation / 90.0) % 4
    x, y = ((left, bottom), (right, bottom), (right, top), (left, top))[quarter]
    return x, y, rotation


def as_pdf(sheet: Sheet) -> bytes:
    """One page the size of the sheet: the tender drawing in grey, the layout in colour."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.collections import EllipseCollection, LineCollection

    x0, y0, x1, y1 = sheet.page
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    figure = plt.figure(figsize=(width / 25.4, height / 25.4))
    try:
        axes = figure.add_axes((0, 0, 1, 1))
        axes.set_xlim(x0, x1)
        axes.set_ylim(y1, y0)  # sheet y points down
        axes.set_axis_off()
        figure.patch.set_facecolor("white")

        lines = _tender_lines(sheet.table)
        if lines:
            axes.add_collection(LineCollection(lines, colors="0.55", linewidths=0.25))
        circles = _tender_circles(sheet.table)
        if circles:
            axes.add_collection(
                EllipseCollection(
                    [2 * r for _, _, r in circles],
                    [2 * r for _, _, r in circles],
                    0,
                    units="xy",
                    offsets=[(cx, cy) for cx, cy, _ in circles],
                    offset_transform=axes.transData,
                    facecolors="none",
                    edgecolors="0.55",
                    linewidths=0.25,
                )
            )
        for span in geometry.texts(sheet.table):
            if not span.get("text"):
                continue
            size = float(span.get("height") or (span["maxy"] - span["miny"]) or 2.0)
            x, y, rotation = _written_from(span)
            axes.text(
                x,
                y,
                str(span["text"]),
                fontsize=max(size, 0.5) * 72 / 25.4 * 0.8,
                color="0.45",
                va="bottom",
                ha="left",
                rotation=rotation,
                rotation_mode="anchor",
                clip_on=True,
            )

        if sheet.scope:
            outline = [*sheet.scope, sheet.scope[0]]
            axes.plot(
                [p[0] for p in outline],
                [p[1] for p in outline],
                color="#1a7f37",
                linewidth=0.6,
                linestyle=(0, (6, 3)),
            )
        for part, colour in (("range", "#0b5cad"), ("feed", "#7a3db8")):
            runs = [pipe for pipe in sheet.pipes if pipe.part == part]
            if runs:
                axes.add_collection(
                    LineCollection([pipe.points for pipe in runs], colors=colour, linewidths=0.7)
                )
            for pipe in runs:
                if pipe.dn is None or len(pipe.points) < 2:
                    continue
                (ax, ay), (bx, by) = pipe.points[0], pipe.points[-1]
                axes.text(
                    (ax + bx) / 2,
                    (ay + by) / 2 - 0.4,
                    f"DN{pipe.dn}",
                    fontsize=3.0,
                    color=colour,
                    ha="center",
                    va="bottom",
                )
        if sheet.heads:
            axes.add_collection(
                EllipseCollection(
                    [2 * HEAD_RADIUS_MM] * len(sheet.heads),
                    [2 * HEAD_RADIUS_MM] * len(sheet.heads),
                    0,
                    units="xy",
                    offsets=[(head.x, head.y) for head in sheet.heads],
                    offset_transform=axes.transData,
                    facecolors="none",
                    edgecolors="#c1121f",
                    linewidths=0.6,
                )
            )

        # The stamp: across the sheet, and with what the layout rests on in a corner.
        axes.text(
            (x0 + x1) / 2,
            (y0 + y1) / 2,
            STAMP.upper(),
            fontsize=min(width, height) * 72 / 25.4 * 0.05,
            color="#c1121f",
            alpha=0.18,
            ha="center",
            va="center",
            rotation=math.degrees(math.atan2(height, width)),
            weight="bold",
        )
        notes = _note_lines(sheet)
        axes.text(
            x0 + 0.012 * width,
            y1 - 0.012 * height,
            "\n".join(notes),
            fontsize=max(6.0, min(width, height) * 72 / 25.4 * 0.011),
            color="#c1121f",
            ha="left",
            va="bottom",
            linespacing=1.4,
            bbox={"facecolor": "white", "edgecolor": "#c1121f", "linewidth": 0.8, "pad": 6},
        )
        buffer = io.BytesIO()
        figure.savefig(
            buffer,
            format="pdf",
            metadata={
                "Title": f"{sheet.number}: proposed sprinkler layout ({STAMP})",
                "Subject": STAMP,
                "Creator": "FireBid SG",
            },
        )
        return buffer.getvalue()
    finally:
        plt.close(figure)


# AutoCAD colour indices for the layers: grey, red, blue, green, red.
LAYER_COLOURS = {
    LAYER_TENDER: 8,
    LAYER_HEADS: 1,
    LAYER_PIPE: 5,
    LAYER_SCOPE: 3,
    LAYER_STAMP: 1,
}


def as_dxf(sheet: Sheet) -> bytes:
    """The same drawing as layers of a DXF, in sheet millimetres with y up as CAD has it.

    Written as DXF R12, entity by entity as it goes: every CAD program opens it, and a sheet
    of a million pieces is written without first being built in memory. Coordinates are to a
    hundredth of a millimetre on the sheet, which is finer than the drawing was plotted.
    """
    from ezdxf.addons import r12writer

    x0, _, x1, y1 = sheet.page
    width, height = max(x1 - x0, 1.0), max(y1 - sheet.page[1], 1.0)

    def up(x: float, y: float) -> tuple[float, float]:
        return (round(x - x0, 2), round(y1 - y, 2))

    stream = io.StringIO()
    with r12writer(stream) as dxf:

        def write(
            text: str, at: tuple[float, float], size: float, layer: str, turn: float = 0.0
        ) -> None:
            dxf.add_text(
                text,
                at,
                height=round(size, 2),
                rotation=turn,
                layer=layer,
                color=LAYER_COLOURS[layer],
            )

        grey = LAYER_COLOURS[LAYER_TENDER]
        for run in _tender_lines(sheet.table):
            points = [up(float(x), float(y)) for x, y in run]
            if len(points) == 2:
                dxf.add_line(points[0], points[1], layer=LAYER_TENDER, color=grey)
            else:
                dxf.add_polyline(points, layer=LAYER_TENDER, color=grey)
        for cx, cy, radius in _tender_circles(sheet.table):
            dxf.add_circle(up(cx, cy), round(radius, 2), layer=LAYER_TENDER, color=grey)
        for span in geometry.texts(sheet.table):
            if not span.get("text"):
                continue
            size = float(span.get("height") or (span["maxy"] - span["miny"]) or 2.0)
            x, y, rotation = _written_from(span)
            write(str(span["text"]), up(x, y), max(size, 0.5), LAYER_TENDER, rotation)

        if sheet.scope:
            dxf.add_polyline(
                [up(x, y) for x, y in sheet.scope],
                closed=True,
                layer=LAYER_SCOPE,
                color=LAYER_COLOURS[LAYER_SCOPE],
            )
        blue = LAYER_COLOURS[LAYER_PIPE]
        for pipe in sheet.pipes:
            if len(pipe.points) < 2:
                continue
            dxf.add_polyline([up(x, y) for x, y in pipe.points], layer=LAYER_PIPE, color=blue)
            if pipe.dn is not None:
                (ax, ay), (bx, by) = pipe.points[0], pipe.points[-1]
                write(
                    f"DN{pipe.dn} {pipe.part}",
                    up((ax + bx) / 2, (ay + by) / 2 - 0.4),
                    1.2,
                    LAYER_PIPE,
                )
        red = LAYER_COLOURS[LAYER_HEADS]
        for head in sheet.heads:
            dxf.add_circle(up(head.x, head.y), HEAD_RADIUS_MM, layer=LAYER_HEADS, color=red)
            dxf.add_point(up(head.x, head.y), layer=LAYER_HEADS, color=red)

        write(
            STAMP.upper(),
            (round(0.12 * width, 2), round(0.08 * height, 2)),
            min(width, height) * 0.04,
            LAYER_STAMP,
            round(math.degrees(math.atan2(height, width)), 2),
        )
        line_height = max(2.5, min(width, height) * 0.008)
        notes = _note_lines(sheet)
        for index, note in enumerate(notes):
            at = (
                round(0.012 * width, 2),
                round(0.012 * height + (len(notes) - 1 - index) * line_height * 1.6, 2),
            )
            write(note, at, line_height, LAYER_STAMP)
    return stream.getvalue().encode("utf-8")


def render(sheet: Sheet, fmt: str) -> bytes:
    if fmt == "pdf":
        return as_pdf(sheet)
    if fmt == "dxf":
        return as_dxf(sheet)
    raise ValueError(f"a layout is exported as {' or '.join(FORMATS)}, not {fmt!r}")

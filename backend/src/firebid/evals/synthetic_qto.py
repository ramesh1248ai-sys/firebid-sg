"""The synthetic installation as sheets that repeat each other: the duplicates P1-07 must find.

The installation is `synthetic_network`'s, one floor of a wet-pipe system, now as a list of
elements (symbols, pipes, annotations) that can be drawn whole or in part, at any scale:

* the **general arrangement**, all of it, at 1:100;
* an **enlarged plan** of the riser area at 1:50: the riser, both valves and the first two
  branches' lower heads, again;
* a **match-lined pair** of 1:100 plans that together cover the floor and overlap on the
  branch at 9 m: its heads and pipe are on both;
* a **riser schematic**, not to scale, showing the riser's valves again.

Every sheet of a tender repeats something another shows. After de-duplication the tender's
counts and lengths are the installation's, once.
"""

from __future__ import annotations

from dataclasses import dataclass

from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as symbols
from firebid.evals.synthetic import LAYER_PIPE, LAYER_TEXT, SHEET_ORIGIN, SHEET_SIZE
from firebid.evals.synthetic_network import (
    BRANCH_HEADS,
    BRANCH_XS,
    CHECK_X,
    GATE_X,
    HEAD_YS,
    MAIN_END_X,
    MAIN_Y,
    NETWORK,
    REDUCER_X,
    RISER_X,
    TYPES,
    R,
)

Box = tuple[float, float, float, float]  # drawing units: x0, y0, x1, y1
EVERYWHERE: Box = (-1e9, -1e9, 1e9, 1e9)


@dataclass(frozen=True)
class Symbol:
    block: str
    x: float
    y: float
    rotation: float = 0.0


@dataclass(frozen=True)
class Pipe:
    x0: float
    y0: float
    x1: float
    y1: float
    dn: int


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    rotation: float = 0.0


def installation() -> tuple[list[Symbol], list[Pipe], list[Label]]:
    """The floor's installation, element by element: the same as `network_plan()`."""
    placed = [
        Symbol("RSR", RISER_X, MAIN_Y),
        Symbol("VLV-GATE", GATE_X, MAIN_Y),
        Symbol("VLV-CHK", CHECK_X, MAIN_Y),
        Symbol("FTG-RED", REDUCER_X, MAIN_Y),
    ]
    stops = [RISER_X + R * 0.8, GATE_X - R, GATE_X + R, CHECK_X - R, CHECK_X + R, REDUCER_X - R]
    pipes = [
        Pipe(x0, MAIN_Y, x1, MAIN_Y, 150) for x0, x1 in zip(stops[0::2], stops[1::2], strict=True)
    ]
    pipes.append(Pipe(REDUCER_X + R, MAIN_Y, MAIN_END_X, MAIN_Y, 100))
    labels = [
        Label("RISER R1", RISER_X - 600, MAIN_Y + 500),
        Label("150Ø", 4_200, MAIN_Y + 250),
        Label("DN100", 13_300, MAIN_Y + 250),
    ]
    for x, head in zip(BRANCH_XS, BRANCH_HEADS, strict=True):
        pipes.append(Pipe(x, MAIN_Y, x, HEAD_YS[-1], 50))
        placed.extend(Symbol(head, x, y, 90.0 if head == "SPK-SW" else 0.0) for y in HEAD_YS)
        labels.append(Label("DN50", x - 450, 7_000, 90))
    return placed, pipes, labels


def _inside(x: float, y: float, box: Box) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _clip(pipe: Pipe, box: Box) -> Pipe | None:
    """A horizontal or vertical pipe cut to the box, or None if it misses it."""
    x0, x1 = sorted((pipe.x0, pipe.x1))
    y0, y1 = sorted((pipe.y0, pipe.y1))
    cx0, cx1 = max(x0, box[0]), min(x1, box[2])
    cy0, cy1 = max(y0, box[1]), min(y1, box[3])
    if cx0 > cx1 or cy0 > cy1 or (cx0 == cx1 and cy0 == cy1):
        return None
    return Pipe(cx0, cy0, cx1, cy1, pipe.dn)


@dataclass
class SheetTruth:
    """What is drawn on one sheet, in installation terms (drawing-unit positions)."""

    symbols: list[Symbol]
    pipes: list[Pipe]


def draw(space: Modelspace, region: Box = EVERYWHERE) -> SheetTruth:
    placed, pipes, labels = installation()
    drawn = SheetTruth([], [])
    for symbol in placed:
        if _inside(symbol.x, symbol.y, region):
            symbols._insert(space, symbol.block, symbol.x, symbol.y, symbol.rotation)
            drawn.symbols.append(symbol)
    for pipe in pipes:
        cut = _clip(pipe, region)
        if cut is not None:
            space.add_line((cut.x0, cut.y0), (cut.x1, cut.y1), dxfattribs={"layer": LAYER_PIPE})
            drawn.pipes.append(cut)
    for label in labels:
        if _inside(label.x, label.y, region):
            space.add_text(
                label.text, height=250, rotation=label.rotation, dxfattribs={"layer": LAYER_TEXT}
            ).set_placement((label.x, label.y))
    return drawn


def _sheet(
    number: str,
    region: Box,
    *,
    title: str,
    drawing_scale: int = 100,
    with_legend: bool = True,
    note: str | None = None,
) -> tuple[Drawing, SheetTruth]:
    document, space = synthetic._new_drawing()
    symbols._define(document, NETWORK)
    drawn = draw(space, region)
    scale = f"1:{drawing_scale}"
    if region == EVERYWHERE or drawing_scale == 100:
        synthetic._structural_grid(space, 8, 6, drawing_scale=drawing_scale)
        synthetic._dimensions(space)
    else:
        # An enlarged plan shows the grid lines around its area, not the whole floor's.
        synthetic._structural_grid(space, 8, 6, drawing_scale=drawing_scale, region=region)
    synthetic._view_title(space, title, scale, drawing_scale=drawing_scale)
    factor = drawing_scale / 100
    if with_legend:
        right = (SHEET_ORIGIN[0] + SHEET_SIZE[0] - 11_000) * factor
        top = (SHEET_ORIGIN[1] + SHEET_SIZE[1] - 2_000) * factor
        symbols._legend(space, NETWORK, right, top, pitch=1_000)
    if note:
        space.add_text(note, height=250 * factor, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (SHEET_ORIGIN[0] * factor + 1_500 * factor, SHEET_ORIGIN[1] * factor + 1_500 * factor)
        )
    synthetic._title_block(
        space,
        number,
        "R01",
        scale,
        title=title,
        drawing_scale=drawing_scale,
        consultant=NETWORK.name,
    )
    return document, drawn


def car_park_plan(
    number: str = "FP-B1-201", notes: tuple[str, ...] = ()
) -> tuple[Drawing, SheetTruth]:
    """The same installation as a basement car park plan, with general notes on it: what
    the specification is cross-checked against (P2-03)."""
    document, drawn = _sheet(number, EVERYWHERE, title="BASEMENT 1 CAR PARK SPRINKLER LAYOUT PLAN")
    space = document.modelspace()
    for index, note in enumerate(notes):
        space.add_text(note, height=250, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (SHEET_ORIGIN[0] + 1_500, SHEET_ORIGIN[1] + 2_500 - index * 500)
        )
    return document, drawn


# A ceiling height note, as an architect's reflected ceiling plan or a section states it.
CEILING_NOTE = "CEILING HEIGHT 2750"


def general_arrangement(number: str = "FP-L05-201") -> tuple[Drawing, SheetTruth]:
    return _sheet(number, EVERYWHERE, title="LEVEL 5 SPRINKLER LAYOUT PLAN", note=CEILING_NOTE)


# The riser area: the riser, both valves, the first two branches up to their second heads,
# between grid lines A and B, 1 and 3.
ENLARGED_REGION: Box = (0.0, 1_000.0, 6_500.0, 9_500.0)


def enlarged_plan(number: str = "FP-L05-301") -> tuple[Drawing, SheetTruth]:
    return _sheet(
        number,
        ENLARGED_REGION,
        title="ENLARGED PLAN - RISER AREA",
        drawing_scale=50,
        with_legend=False,
    )


# The match line at 9 m: west sheet to 10 m, east sheet from 8 m; the branch at 9 m is on both.
WEST_REGION: Box = (-1e9, -1e9, 10_000.0, 1e9)
EAST_REGION: Box = (8_000.0, -1e9, 1e9, 1e9)


def match_lined_pair() -> tuple[tuple[Drawing, SheetTruth], tuple[Drawing, SheetTruth]]:
    return (
        _sheet(
            "FP-L05-202",
            WEST_REGION,
            title="LEVEL 5 SPRINKLER LAYOUT PLAN - PART 1",
            note="MATCH LINE - SEE FP-L05-203",
        ),
        _sheet(
            "FP-L05-203",
            EAST_REGION,
            title="LEVEL 5 SPRINKLER LAYOUT PLAN - PART 2",
            with_legend=False,
            note="MATCH LINE - SEE FP-L05-202",
        ),
    )


def riser_schematic(number: str = "FP-SCH-001") -> tuple[Drawing, SheetTruth]:
    """The riser's valves on a schematic: drawn again, not to scale, never counted twice."""
    document, space = synthetic._new_drawing()
    symbols._define(document, NETWORK)
    drawn = SheetTruth([], [])
    x = 3_000.0
    space.add_line((x, 1_000), (x, 16_000), dxfattribs={"layer": LAYER_PIPE})
    for block, y in (("VLV-GATE", 4_000.0), ("VLV-CHK", 6_000.0)):
        symbols._insert(space, block, x, y, 90.0)
        drawn.symbols.append(Symbol(block, x, y, 90.0))
    for index, level in enumerate(("L03", "L04", "L05")):
        space.add_text(level, height=300, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (x + 800, 8_000 + index * 3_000)
        )
    space.add_text(
        "SPRINKLER RISER SCHEMATIC", height=350, dxfattribs={"layer": LAYER_TEXT}
    ).set_placement((1_000, 20_000))
    space.add_text("NOT TO SCALE", height=250, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (1_000, 19_300)
    )
    synthetic._title_block(
        space, number, "R01", "NTS", title="SPRINKLER RISER SCHEMATIC", consultant=NETWORK.name
    )
    return document, drawn


def counted(drawn: SheetTruth) -> dict[str, int]:
    """What a sheet shows, in object types (the riser is pipe, not a counted object)."""
    out: dict[str, int] = {}
    for symbol in drawn.symbols:
        kind = TYPES[symbol.block]
        if kind != "pipe":
            out[kind] = out.get(kind, 0) + 1
    return out

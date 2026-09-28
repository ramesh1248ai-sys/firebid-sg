"""Synthetic symbols, legends and plans from two consultants (P1-04).

Each consultant draws the same canonical objects its own way: its own block names and its
own graphics. A tender from a consultant is a legend sheet (each symbol beside what it is)
and a plan that uses the same blocks, some rotated and scaled, plus one symbol its legend
never explains. That last one is the trap: it must be raised as unmapped, never counted.

The plan also carries a small legend of its own, as many consultants' plans do. Those
example symbols are not installed items, so they must not be counted either.

At 1:100, symbols are drawn about 5 mm across on paper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ezdxf.document import Drawing
from ezdxf.layouts import BlockLayout, Modelspace

from firebid.evals import synthetic
from firebid.evals.synthetic import LAYER_TEXT, SHEET_ORIGIN, SHEET_SIZE

R = 250.0  # a symbol's radius in drawing units: 2.5 mm on paper at 1:100
LAYER_SYMBOL = "FP-SYMBOL"


@dataclass(frozen=True)
class Symbol:
    block: str
    description: str
    object_type: str  # the canonical type a person would confirm it as
    draw: str  # which graphic below


@dataclass(frozen=True)
class Consultant:
    name: str
    symbols: tuple[Symbol, ...]
    mystery_block: str = "UNK-01"  # used on the plan, explained nowhere


ALPHA = Consultant(
    name="ALPHA CONSULTANTS PTE LTD",
    symbols=(
        Symbol("SPK-PEND", "PENDENT SPRINKLER", "sprinkler_pendent", "circle_cross"),
        # No keyword rule reads "UP TYPE": this row goes to the model.
        Symbol("SPK-UP", "SPRINKLER - UP TYPE", "sprinkler_upright", "circle_dot"),
        Symbol("SPK-SW", "SIDEWALL SPRINKLER", "sprinkler_sidewall", "half_circle"),
        Symbol("VLV-GATE", "GATE VALVE", "gate_valve", "bow_tie"),
        Symbol("VLV-CHK", "NON-RETURN VALVE", "check_valve", "arrow_bar"),
        Symbol("DEV-FS", "FLOW SWITCH", "flow_switch", "square_diagonal"),
    ),
)

BETA = Consultant(
    name="BETA ENGINEERING PTE LTD",
    symbols=(
        Symbol("P-SPR", "SPRINKLER HEAD - PENDENT TYPE", "sprinkler_pendent", "circle_bar"),
        Symbol("GV", "GATE VALVE (OS&Y)", "gate_valve", "square_cross"),
    ),
    mystery_block="X-99",
)


def _graphic(block: BlockLayout, name: str) -> None:
    """One symbol's line work, in block coordinates around (0, 0), on layer 0."""
    attributes = {"layer": "0"}
    if name == "circle_cross":
        block.add_circle((0, 0), R, dxfattribs=attributes)
        d = R * math.sqrt(0.5)
        block.add_line((-d, -d), (d, d), dxfattribs=attributes)
        block.add_line((-d, d), (d, -d), dxfattribs=attributes)
    elif name == "circle_dot":
        block.add_circle((0, 0), R, dxfattribs=attributes)
        block.add_circle((0, 0), R * 0.35, dxfattribs=attributes)
    elif name == "half_circle":
        block.add_arc((0, 0), R, 0, 180, dxfattribs=attributes)
        block.add_line((-R, 0), (R, 0), dxfattribs=attributes)
        block.add_line((0, 0), (0, -R * 0.6), dxfattribs=attributes)
    elif name == "bow_tie":
        block.add_lwpolyline([(-R, -R / 2), (-R, R / 2), (0, 0)], close=True, dxfattribs=attributes)
        block.add_lwpolyline([(R, -R / 2), (R, R / 2), (0, 0)], close=True, dxfattribs=attributes)
    elif name == "arrow_bar":
        block.add_lwpolyline([(-R, -R / 2), (-R, R / 2), (R, 0)], close=True, dxfattribs=attributes)
        block.add_line((R, -R / 2), (R, R / 2), dxfattribs=attributes)
    elif name == "square_diagonal":
        side = R * 0.8
        block.add_lwpolyline(
            [(-side, -side), (side, -side), (side, side), (-side, side)],
            close=True,
            dxfattribs=attributes,
        )
        block.add_line((-side, -side), (side, side), dxfattribs=attributes)
    elif name == "circle_bar":
        block.add_circle((0, 0), R, dxfattribs=attributes)
        block.add_line((-R, 0), (R, 0), dxfattribs=attributes)
    elif name == "square_cross":
        side = R * 0.8
        block.add_lwpolyline(
            [(-side, -side), (side, -side), (side, side), (-side, side)],
            close=True,
            dxfattribs=attributes,
        )
        block.add_line((-side, -side), (side, side), dxfattribs=attributes)
        block.add_line((-side, side), (side, -side), dxfattribs=attributes)
    elif name == "reducer":  # a tapered body: where a run's size changes
        block.add_lwpolyline(
            [(-R, -R * 0.6), (R, -R * 0.3), (R, R * 0.3), (-R, R * 0.6)],
            close=True,
            dxfattribs=attributes,
        )
    elif name == "riser":  # a square with a circle: a pipe rising through the floor
        side = R * 0.8
        block.add_lwpolyline(
            [(-side, -side), (side, -side), (side, side), (-side, side)],
            close=True,
            dxfattribs=attributes,
        )
        block.add_circle((0, 0), R * 0.45, dxfattribs=attributes)
    elif name == "star":  # the mystery symbol
        for angle in range(0, 180, 45):
            dx, dy = R * math.cos(math.radians(angle)), R * math.sin(math.radians(angle))
            block.add_line((-dx, -dy), (dx, dy), dxfattribs=attributes)
    else:
        raise ValueError(f"no graphic called {name!r}")


def _define(document: Drawing, consultant: Consultant) -> None:
    if LAYER_SYMBOL not in document.layers:
        document.layers.add(LAYER_SYMBOL, color=1)
    for symbol in consultant.symbols:
        _graphic(document.blocks.new(symbol.block), symbol.draw)
    _graphic(document.blocks.new(consultant.mystery_block), "star")


# A consultant whose legend has no LEGEND heading: category columns side by side, as on many
# Singapore tender sets ("FIRE FIGHTING & ALARM SYSTEM", "VALVES & ACCESSORIES").
GAMMA = Consultant(
    name="GAMMA CONSULTING ENGINEERS PTE LTD",
    symbols=(
        Symbol("G-EXP", "EXPOSED SPRINKLER", "sprinkler_pendent", "circle_cross"),
        Symbol("G-CON", "CONCEALED SPRINKLER", "sprinkler_pendent", "circle_dot"),
        Symbol("G-SW", "SIDE-WALL SPRINKLER", "sprinkler_sidewall", "half_circle"),
        Symbol("G-FS", "FLOW SWITCH", "flow_switch", "square_diagonal"),
        Symbol("G-GV", "GATE VALVE", "gate_valve", "bow_tie"),
        Symbol("G-CV", "CHECK VALVE", "check_valve", "arrow_bar"),
        Symbol("G-BV", "BALL VALVE", "gate_valve", "circle_bar"),
        Symbol("G-ST", "STRAINER", "strainer", "square_cross"),
    ),
)
GAMMA_COLUMNS = (
    ("FIRE SPRINKLER SYSTEM", ("G-EXP", "G-CON", "G-SW", "G-FS")),
    ("VALVES & ACCESSORIES", ("G-GV", "G-CV", "G-BV", "G-ST")),
)


def category_legend_sheet(
    consultant: Consultant = GAMMA,
    columns: tuple[tuple[str, tuple[str, ...]], ...] = GAMMA_COLUMNS,
    sheet_number: str = "FP-LEG-002",
) -> Drawing:
    """A legend in category columns with no LEGEND heading, a stamp above the columns, and
    a stack of pipe sizes and room codes beside symbols that is not a legend at all."""
    document, space = synthetic._new_drawing()
    _define(document, consultant)
    described = {symbol.block: symbol.description for symbol in consultant.symbols}
    left = SHEET_ORIGIN[0] + 2_000
    top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 3_000
    space.add_text(
        "PLEASE DELETE ACCORDINGLY", height=450, dxfattribs={"layer": LAYER_TEXT}
    ).set_placement((left + 6_000, top + 1_200))
    for index, (heading, blocks) in enumerate(columns):
        x = left + index * 9_000
        space.add_text(heading, height=350, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (x, top)
        )
        for row, block in enumerate(blocks):
            y = top - (row + 1.5) * 1_000
            _insert(space, block, x + R, y)
            space.add_text(
                described[block], height=250, dxfattribs={"layer": LAYER_TEXT}
            ).set_placement((x + 6 * R, y - 100))
    # Not a legend: codes stacked beside symbols under a short upper-case label.
    x = left + 22_000
    space.add_text("PIPE SIZES", height=350, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (x, top)
    )
    for row, code in enumerate(("DN100", "L17-TW.B-30", "DN50", "RM 4")):
        y = top - (row + 1.5) * 1_000
        _insert(space, consultant.symbols[0].block, x + R, y)
        space.add_text(code, height=250, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (x + 6 * R, y - 100)
        )
    synthetic._title_block(
        space, sheet_number, "-", "AS SHOWN", title="NOTES & LEGEND", consultant=consultant.name
    )
    return document


def _insert(
    space: Modelspace, block: str, x: float, y: float, rotation: float = 0.0, scale: float = 1.0
) -> None:
    space.add_blockref(
        block,
        (x, y),
        dxfattribs={
            "layer": LAYER_SYMBOL,
            "rotation": rotation,
            "xscale": scale,
            "yscale": scale,
        },
    )


def _legend(space: Modelspace, consultant: Consultant, x: float, top: float, pitch: float) -> None:
    """A LEGEND heading and a row per symbol: the graphic, then what it is."""
    space.add_text("LEGEND", height=350, dxfattribs={"layer": LAYER_TEXT}).set_placement((x, top))
    for index, symbol in enumerate(consultant.symbols):
        y = top - (index + 1.5) * pitch
        _insert(space, symbol.block, x + R, y)
        space.add_text(
            symbol.description, height=200, dxfattribs={"layer": LAYER_TEXT}
        ).set_placement((x + 3 * R, y - 100))


@dataclass
class PlanTruth:
    """What a person counting the plan would find, legend examples excluded."""

    counts: dict[str, int] = field(default_factory=dict)
    unmapped_block: str = ""
    unmapped_count: int = 0


def legend_sheet(consultant: Consultant, sheet_number: str = "FP-LEG-001") -> Drawing:
    document, space = synthetic._new_drawing()
    _define(document, consultant)
    left = SHEET_ORIGIN[0] + 2_000
    top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 3_000
    _legend(space, consultant, left, top, pitch=1_500)
    synthetic._title_block(
        space, sheet_number, "R01", "NTS", title="LEGEND AND SYMBOLS", consultant=consultant.name
    )
    return document


# Where each symbol is installed on the plan, and how it is turned and sized. Rotations and
# scales are deliberately awkward: a match must not depend on either.
PLACEMENTS = (
    (0.0, 1.0),
    (37.0, 1.0),
    (90.0, 1.6),
    (211.0, 0.7),
)


def plan_sheet(
    consultant: Consultant, sheet_number: str = "FP-L05-201", *, with_legend: bool = True
) -> tuple[Drawing, PlanTruth]:
    """A plan: each legend symbol installed at every placement, a row per symbol, plus the
    mystery symbol three times. With its own small legend in the top-right corner."""
    document, space = synthetic._new_drawing()
    _define(document, consultant)
    truth = PlanTruth(unmapped_block=consultant.mystery_block)
    for row, symbol in enumerate(consultant.symbols):
        for column, (rotation, scale) in enumerate(PLACEMENTS):
            _insert(
                space, symbol.block, 2_000 + column * 3_000, 3_000 + row * 2_500, rotation, scale
            )
        truth.counts[symbol.object_type] = truth.counts.get(symbol.object_type, 0) + len(PLACEMENTS)
    for column in range(3):
        _insert(space, consultant.mystery_block, 16_000 + column * 2_500, 3_000, 15.0 * column)
    truth.unmapped_count = 3
    if with_legend:
        right = SHEET_ORIGIN[0] + SHEET_SIZE[0] - 11_000
        top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 2_000
        _legend(space, consultant, right, top, pitch=1_100)
    synthetic._title_block(
        space,
        sheet_number,
        "R01",
        "1:100",
        title="FIRE SPRINKLER LAYOUT",
        consultant=consultant.name,
    )
    return document, truth

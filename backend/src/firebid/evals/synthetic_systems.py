"""A synthetic tender of the Phase 2 systems: pump room, riser schematic, floor, site (P2-01).

Four sheets from consultant Delta, drawn at 1:100 the way a Singapore tender set shows what
is not sprinklers:

* **`FP-B1-101`, the fire pump room:** a fire water tank, a duty and a standby fire pump and
  a jockey pump on a suction header and a discharge header that ends at the wet rising
  main, a pump control panel, a test header, a dry-pipe valve set with its air compressor,
  a pre-action and a deluge valve set. Every piece of equipment is tagged, and a FIRE PUMP
  SCHEDULE gives each pump's duty, flow (in L/s), head and power.
* **`FP-SCH-002`, the riser schematic:** not to scale. The rising main with a landing valve
  and a hose reel at each of five levels, the breeching inlet at its foot, and both fire
  pumps again. Each level is named with its finished floor level. The breeching inlet is
  on no plan: it is counted from here. The pumps, landing valves and hose reels are on
  plans, and are not counted twice.
* **`FP-L03-401`, a typical floor:** the rising main's riser symbol, a landing valve on a
  DN100 pipe from it, and two hose reels.
* **`FP-SITE-001`, the site plan:** three pillar hydrants on spurs off a DN150 hydrant main.

The truth is what is drawn: each sheet's symbols by type, and its pipe by size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ezdxf.document import Drawing
from ezdxf.layouts import BlockLayout, Modelspace

from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as symbols
from firebid.evals.synthetic import LAYER_PIPE, LAYER_TEXT, SHEET_ORIGIN, SHEET_SIZE

R = symbols.R
Symbol = symbols.Symbol

DELTA = symbols.Consultant(
    name="DELTA M&E CONSULTANTS PTE LTD",
    symbols=(
        Symbol("EQ-FP", "FIRE PUMP", "fire_pump", "pump"),
        Symbol("EQ-JP", "JOCKEY PUMP", "jockey_pump", "jockey"),
        Symbol("EQ-PC", "PUMP CONTROL PANEL", "pump_controller", "panel"),
        Symbol("EQ-TK", "FIRE WATER TANK", "fire_water_tank", "tank"),
        Symbol("EQ-BI", "BREECHING INLET", "breeching_inlet", "breeching"),
        Symbol("EQ-LV", "LANDING VALVE", "landing_valve", "landing"),
        Symbol("EQ-FH", "PILLAR HYDRANT", "hydrant", "hydrant"),
        Symbol("EQ-HR", "HOSE REEL", "hose_reel", "reel"),
        Symbol("EQ-TH", "TEST HEADER", "test_header", "header"),
        Symbol("VS-DP", "DRY PIPE VALVE SET", "dry_pipe_valve_set", "set_circle"),
        Symbol("VS-PA", "PRE-ACTION VALVE SET", "pre_action_valve_set", "set_square"),
        Symbol("VS-DL", "DELUGE VALVE SET", "deluge_valve_set", "set_bar"),
        Symbol("EQ-AC", "AIR COMPRESSOR", "air_compressor", "compressor"),
        Symbol("RSR-W", "WET RISING MAIN", "pipe", "riser"),
    ),
    mystery_block="UNK-D1",
)
TYPES = {symbol.block: symbol.object_type for symbol in DELTA.symbols}
DESCRIBED = {symbol.description: symbol.object_type for symbol in DELTA.symbols}

PUMP_ROOM, SCHEMATIC, FLOOR, SITE = "FP-B1-101", "FP-SCH-002", "FP-L03-401", "FP-SITE-001"
# The schedule on the pump room sheet, as drawn: flow in litres a second.
SCHEDULE_HEADING = "FIRE PUMP SCHEDULE"
SCHEDULE_COLUMNS = (
    ("TAG", 1_000.0),
    ("DESCRIPTION", 3_000.0),
    ("DUTY", 8_000.0),
    ("FLOW (L/S)", 10_500.0),
    ("HEAD (M)", 13_500.0),
    ("POWER (KW)", 16_000.0),
)
SCHEDULE_ROWS = (
    ("FP-01", "ELECTRIC FIRE PUMP", "DUTY", "47.5", "80", "75"),
    ("FP-02", "DIESEL FIRE PUMP", "STANDBY", "47.5", "80", "75"),
    ("JP-01", "JOCKEY PUMP", "-", "1.5", "85", "4"),
)
# The schematic's levels, with their finished floor levels in metres.
LEVELS = (
    ("L01", "+0.000"),
    ("L02", "+4.500"),
    ("L03", "+9.000"),
    ("L04", "+13.500"),
    ("L05", "+18.000"),
)
ROOF = ("ROOF", "+22.500")
FLOOR_TO_FLOOR_L03 = 4_500


def _graphic(block: BlockLayout, name: str) -> None:
    """One equipment symbol's line work, around (0, 0), on layer 0."""
    a = {"layer": "0"}

    def box(w: float, h: float) -> None:
        block.add_lwpolyline([(-w, -h), (w, -h), (w, h), (-w, h)], close=True, dxfattribs=a)

    def bow_tie() -> None:
        block.add_lwpolyline([(-R, -R / 2), (-R, R / 2), (0, 0)], close=True, dxfattribs=a)
        block.add_lwpolyline([(R, -R / 2), (R, R / 2), (0, 0)], close=True, dxfattribs=a)

    if name == "pump":
        block.add_circle((0, 0), R, dxfattribs=a)
        block.add_lwpolyline(
            [(-R * 0.5, -R * 0.5), (-R * 0.5, R * 0.5), (R * 0.7, 0)], close=True, dxfattribs=a
        )
    elif name == "jockey":
        block.add_circle((0, 0), R * 0.7, dxfattribs=a)
        block.add_line((-R, -R), (R, -R), dxfattribs=a)
        block.add_line((0, -R), (0, -R * 0.7), dxfattribs=a)
    elif name == "panel":
        box(R, R * 0.6)
        block.add_line((-R, -R * 0.6), (R, R * 0.6), dxfattribs=a)
    elif name == "tank":
        box(R, R * 0.7)
        block.add_line((-R, R * 0.35), (R, R * 0.35), dxfattribs=a)
        block.add_line((-R, R * 0.1), (R, R * 0.1), dxfattribs=a)
    elif name == "breeching":
        block.add_circle((-R * 0.5, 0), R * 0.4, dxfattribs=a)
        block.add_circle((R * 0.5, 0), R * 0.4, dxfattribs=a)
        block.add_line((-R, -R * 0.6), (R, -R * 0.6), dxfattribs=a)
    elif name == "landing":
        block.add_lwpolyline([(-R, R), (R, R), (0, -R * 0.2)], close=True, dxfattribs=a)
        block.add_line((0, -R * 0.2), (0, -R), dxfattribs=a)
    elif name == "hydrant":
        block.add_circle((0, 0), R, dxfattribs=a)
        block.add_line((-R * 0.4, -R * 0.6), (-R * 0.4, R * 0.6), dxfattribs=a)
        block.add_line((R * 0.4, -R * 0.6), (R * 0.4, R * 0.6), dxfattribs=a)
        block.add_line((-R * 0.4, 0), (R * 0.4, 0), dxfattribs=a)
    elif name == "reel":
        block.add_circle((0, 0), R, dxfattribs=a)
        block.add_circle((0, 0), R * 0.6, dxfattribs=a)
        block.add_line((0, 0), (R, R), dxfattribs=a)
    elif name == "header":
        box(R, R * 0.4)
        for x in (-R * 0.5, 0.0, R * 0.5):
            block.add_line((x, R * 0.4), (x, R), dxfattribs=a)
    elif name == "set_circle":
        bow_tie()
        block.add_circle((0, 0), R * 0.3, dxfattribs=a)
    elif name == "set_square":
        bow_tie()
        box(R * 0.3, R * 0.3)
    elif name == "set_bar":
        bow_tie()
        block.add_line((0, -R), (0, R), dxfattribs=a)
    elif name == "compressor":
        block.add_circle((0, 0), R, dxfattribs=a)
        block.add_line((-R * 0.8, R * 0.6), (R * 0.8, R * 0.6), dxfattribs=a)
        block.add_line((-R * 0.8, -R * 0.6), (R * 0.8, -R * 0.6), dxfattribs=a)
    else:
        symbols._graphic(block, name)


def _define(document: Drawing) -> None:
    if symbols.LAYER_SYMBOL not in document.layers:
        document.layers.add(symbols.LAYER_SYMBOL, color=1)
    for symbol in DELTA.symbols:
        _graphic(document.blocks.new(symbol.block), symbol.draw)
    symbols._graphic(document.blocks.new(DELTA.mystery_block), "star")


@dataclass
class SheetTruth:
    """What one sheet draws: installed symbols by type, pipe by size, and their tags."""

    number: str
    counts: dict[str, int] = field(default_factory=dict)
    lengths: dict[int, float] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)  # tag -> object type
    measured: bool = True
    duplicates_of: tuple[str, ...] = ()


class _Sheet:
    """A sheet being drawn, keeping the truth of what is put on it."""

    def __init__(self, number: str) -> None:
        self.document, self.space = synthetic._new_drawing()
        _define(self.document)
        self.truth = SheetTruth(number)

    def place(self, block: str, x: float, y: float, tag: str | None = None) -> None:
        symbols._insert(self.space, block, x, y)
        kind = TYPES[block]
        if kind != "pipe":  # a riser is a vertical pipe, not a counted object
            self.truth.counts[kind] = self.truth.counts.get(kind, 0) + 1
        if tag:
            self.text(tag, x - 350, y + 450, 200)
            self.truth.tags[tag] = kind

    def pipe(self, dn: int, x0: float, y0: float, x1: float, y1: float) -> None:
        self.space.add_line((x0, y0), (x1, y1), dxfattribs={"layer": LAYER_PIPE})
        self.truth.lengths[dn] = self.truth.lengths.get(dn, 0.0) + abs(x1 - x0) + abs(y1 - y0)

    def text(
        self, words: str, x: float, y: float, height: float = 250, rotation: float = 0
    ) -> None:
        self.space.add_text(
            words, height=height, rotation=rotation, dxfattribs={"layer": LAYER_TEXT}
        ).set_placement((x, y))

    def finish(self, title: str, scale: str = "1:100", legend: bool = True) -> None:
        space: Modelspace = self.space
        if scale != "NTS":
            synthetic._structural_grid(space, 8, 6)
            synthetic._dimensions(space)
            synthetic._view_title(space, title, scale)
        if legend:
            right = SHEET_ORIGIN[0] + SHEET_SIZE[0] - 11_000
            top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 2_000
            symbols._legend(space, DELTA, right, top, pitch=800)
        synthetic._title_block(
            space, self.truth.number, "R01", scale, title=title, consultant=DELTA.name
        )


def pump_room(number: str = PUMP_ROOM) -> tuple[Drawing, SheetTruth]:
    sheet = _Sheet(number)
    suction, discharge, pumps_y = 9_000.0, 3_000.0, 6_000.0
    sheet.place("EQ-TK", 2_000, suction, "TK-01")
    sheet.place("EQ-FP", 5_000, pumps_y, "FP-01")
    sheet.place("EQ-FP", 8_000, pumps_y, "FP-02")
    sheet.place("EQ-JP", 11_000, pumps_y, "JP-01")
    # Suction: a DN200 header from the tank, turning down to the jockey pump; a DN150 spur
    # down to each fire pump.
    sheet.pipe(200, 2_000 + R, suction, 11_000, suction)
    sheet.pipe(200, 11_000, suction, 11_000, pumps_y + R)
    sheet.text("DN200", 6_200, suction + 250)
    for x in (5_000.0, 8_000.0):
        sheet.pipe(150, x, suction, x, pumps_y + R)
        sheet.text("DN150", x - 450, 7_200, rotation=90)
    # Discharge: the duty pump's DN150 turns into the header, which runs to the rising main;
    # the standby pump's and the jockey pump's join it.
    sheet.pipe(150, 5_000, pumps_y - R, 5_000, discharge)
    sheet.pipe(150, 5_000, discharge, 14_000 - R * 0.8, discharge)
    sheet.text("DN150", 12_000, discharge + 250)
    sheet.pipe(150, 8_000, pumps_y - R, 8_000, discharge)
    sheet.text("DN150", 8_000 - 450, 3_900, rotation=90)
    sheet.pipe(50, 11_000, pumps_y - R, 11_000, discharge)
    sheet.text("DN50", 11_000 - 450, 3_900, rotation=90)
    sheet.place("RSR-W", 14_000, discharge)
    sheet.text("RISING MAIN RM1", 14_400, discharge + 500)
    # What stands in the room with no pipe drawn to it.
    sheet.place("EQ-PC", 14_000, 8_000, "PC-01")
    sheet.place("EQ-TH", 17_000, 6_000, "TH-01")
    sheet.place("VS-DP", 17_000, 9_000)
    sheet.place("EQ-AC", 19_000, 9_000, "AC-01")
    sheet.place("VS-PA", 17_000, 11_000)
    sheet.place("VS-DL", 17_000, 13_000)
    # The schedule, above the plan.
    sheet.text(SCHEDULE_HEADING, 1_000, 21_500, 300)
    for heading, x in SCHEDULE_COLUMNS:
        sheet.text(heading, x, 20_800, 200)
    for index, row in enumerate(SCHEDULE_ROWS):
        for (_, x), cell in zip(SCHEDULE_COLUMNS, row, strict=True):
            sheet.text(cell, x, 20_200 - index * 600, 200)
    sheet.finish("FIRE PUMP ROOM LAYOUT PLAN")
    return sheet.document, sheet.truth


def riser_schematic(number: str = SCHEMATIC) -> tuple[Drawing, SheetTruth]:
    sheet = _Sheet(number)
    sheet.truth.measured = False
    sheet.truth.duplicates_of = (PUMP_ROOM, FLOOR)
    x = 3_000.0
    sheet.place("EQ-BI", x, 5_000, "BI-01")
    sheet.space.add_line((x, 5_000 + R), (x, 16_800), dxfattribs={"layer": LAYER_PIPE})
    for index, (level, elevation) in enumerate(LEVELS):
        y = 7_000.0 + index * 2_200
        sheet.place("EQ-LV", x, y)
        sheet.place("EQ-HR", x + 2_000, y)
        sheet.text(f"{level} FFL {elevation}", x + 4_000, y - 100)
    sheet.text(f"{ROOF[0]} FFL {ROOF[1]}", x + 4_000, 17_400)
    sheet.place("EQ-FP", 14_000, 12_000, "FP-01")
    sheet.place("EQ-FP", 16_000, 12_000, "FP-02")
    sheet.text("FIRE PROTECTION RISER SCHEMATIC", 1_000, 20_000, 350)
    sheet.text("NOT TO SCALE", 1_000, 19_300)
    sheet.finish("FIRE PROTECTION RISER SCHEMATIC", "NTS", legend=False)
    return sheet.document, sheet.truth


def floor_plan(number: str = FLOOR) -> tuple[Drawing, SheetTruth]:
    sheet = _Sheet(number)
    sheet.place("RSR-W", 1_000, 3_000)
    sheet.text("RISING MAIN RM1", 400, 3_500)
    sheet.pipe(100, 1_000 + R * 0.8, 3_000, 4_000 - R, 3_000)
    sheet.text("DN100", 2_200, 3_250)
    sheet.place("EQ-LV", 4_000, 3_000, "LV-03")
    sheet.place("EQ-HR", 6_000, 9_000, "HR-31")
    sheet.place("EQ-HR", 15_000, 9_000, "HR-32")
    sheet.finish("LEVEL 3 WET RISER AND HOSE REEL LAYOUT PLAN")
    return sheet.document, sheet.truth


def site_plan(number: str = SITE) -> tuple[Drawing, SheetTruth]:
    sheet = _Sheet(number)
    main_y, hydrant_y = 3_000.0, 6_000.0
    sheet.pipe(150, 1_000, main_y, 19_000, main_y)
    sheet.text("DN150", 7_000, main_y + 250)
    for index, x in enumerate((4_000.0, 10_000.0, 16_000.0)):
        sheet.pipe(150, x, main_y, x, hydrant_y - R)
        sheet.text("DN150", x - 450, 4_000, rotation=90)
        sheet.place("EQ-FH", x, hydrant_y, f"FH-{index + 1}")
    sheet.finish("SITE PLAN - EXTERNAL HYDRANT LAYOUT")
    return sheet.document, sheet.truth


def tender() -> list[tuple[Drawing, SheetTruth]]:
    """The four sheets, plans first."""
    return [pump_room(), floor_plan(), site_plan(), riser_schematic()]

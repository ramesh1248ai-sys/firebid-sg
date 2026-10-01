"""A synthetic design-intent sheet: a base plan, a main, and notes; no heads (P1-12).

What a consultant issues when the detailed design is the contractor's: the architect's plan
in grey, the sprinkler main and its size, and a notes column stating the design criteria.
No sprinkler is drawn. The building is known exactly, so a proposed layout can be checked
against a hand calculation:

* 20 m x 16 m overall (320 m2);
* WARD A, 10 m x 8 m; STORE, 4 m x 3 m; a 3 m x 3 m lift shaft, crossed;
* the rest is open floor, reached through the rooms' doors.
"""

from __future__ import annotations

from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from firebid.evals import synthetic
from firebid.evals.synthetic import LAYER_PIPE, LAYER_TEXT

LAYER_WALL = "A-WALL"
GREY = 0xBBBBBB
DOOR_MM = 900.0
# The building's outline and rooms, in drawing millimetres.
BUILDING = (1_500.0, 1_500.0, 21_500.0, 17_500.0)
WARD = (1_500.0, 9_500.0, 11_500.0, 17_500.0)
STORE = (11_500.0, 14_500.0, 15_500.0, 17_500.0)
LIFT = (18_500.0, 14_500.0, 21_500.0, 17_500.0)
MAIN_Y = 8_500.0

INTENT_NOTE = "THE DESIGN INTENT CONVEYED IN THIS DRAWING SHALL CONSTITUTE THE MINIMUM REQUIREMENT."
NOTES = (
    "GENERAL NOTES",
    INTENT_NOTE,
    "THE CONTRACTOR SHALL BE RESPONSIBLE FOR THE FURTHER DEVELOPMENT AND DETAILED DESIGN.",
    "SPRINKLER DESIGN CRITERIA FOR THE CONCEALED LAYER",
    "(ORDINARY HAZARD GROUP III)",
    "MAXIMUM SPACING: (4M X 3M)",
    "MAXIMUM AREA COVERAGE PER SPRINKLER - 12M2",
    "K-FACTOR : 5.6 (80)",
    "EXPOSED LAYER WITH CEILING HEIGHT > 9M",
    "MAXIMUM SPACING: (3M X 3M)",
    "MAXIMUM AREA COVERAGE PER SPRINKLER - 9M2",
    "K-FACTOR : 11.2",
)


def _wall(space: Modelspace, x0: float, y0: float, x1: float, y1: float) -> None:
    space.add_line((x0, y0), (x1, y1), dxfattribs={"layer": LAYER_WALL, "true_color": GREY})


def _room(
    space: Modelspace,
    box: tuple[float, float, float, float],
    name: str | None,
    *,
    door: bool = True,
) -> None:
    """A rectangular room with a doorway in the middle of its lower wall."""
    x0, y0, x1, y1 = box
    _wall(space, x0, y1, x1, y1)
    _wall(space, x0, y0, x0, y1)
    _wall(space, x1, y0, x1, y1)
    middle = (x0 + x1) / 2
    if door:
        _wall(space, x0, y0, middle - DOOR_MM / 2, y0)
        _wall(space, middle + DOOR_MM / 2, y0, x1, y0)
    else:
        _wall(space, x0, y0, x1, y0)
    if name:
        space.add_text(name, height=250, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (middle - 600, (y0 + y1) / 2)
        )


def design_intent_plan(
    sheet_number: str = "FP-L10-01",
    revision: str = "R01",
    *,
    with_dimensions: bool = True,
    with_notes: bool = True,
) -> Drawing:
    """The sheet. Without dimensions its stated scale has nothing to confirm it."""
    document, space = synthetic._new_drawing()
    if LAYER_WALL not in document.layers:
        document.layers.add(LAYER_WALL, true_color=GREY)
    _room(space, BUILDING, None, door=False)
    _room(space, WARD, "WARD A")
    _room(space, STORE, "STORE")
    _room(space, LIFT, None, door=False)
    _wall(space, LIFT[0], LIFT[1], LIFT[2], LIFT[3])
    _wall(space, LIFT[0], LIFT[3], LIFT[2], LIFT[1])

    space.add_line(
        (BUILDING[0] + 500, MAIN_Y), (BUILDING[2] - 500, MAIN_Y), dxfattribs={"layer": LAYER_PIPE}
    )
    space.add_text("DN150 SPR", height=180, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (6_000, MAIN_Y + 200)
    )
    synthetic._structural_grid(space, 8, 6)
    if with_dimensions:
        synthetic._dimensions(space)
    synthetic._view_title(space, "LEVEL 10 FIRE PROTECTION LAYOUT PLAN", "1:100")
    if with_notes:
        left = synthetic.SHEET_ORIGIN[0] + synthetic.SHEET_SIZE[0] - 13_500
        top = synthetic.SHEET_ORIGIN[1] + synthetic.SHEET_SIZE[1] - 2_000
        for index, line in enumerate(NOTES):
            space.add_text(line, height=200, dxfattribs={"layer": LAYER_TEXT}).set_placement(
                (left, top - index * 450)
            )
    synthetic._title_block(space, sheet_number, revision, "1:100", "FIRE PROTECTION LAYOUT")
    return document

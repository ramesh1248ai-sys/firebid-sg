"""Synthetic fire protection drawings with known ground truth.

CI cannot hold a client's tender, so it needs drawings it is allowed to keep. These are
generated from a seed, which makes the seed the artefact: the fixtures themselves are
regenerated rather than committed, and two machines given the same seed produce byte-identical
DXF geometry.

They are deliberately simple — a grid of sprinklers on a branch, a main with a size
annotation. They prove the *pipeline* reads geometry, counts symbols and follows revisions.
They cannot prove the platform reads a real consultant's drawing, which is what decision D3's
golden set is for. Nothing here should ever be quoted as an accuracy figure.

The seven fixture types (P0-05 item 6):

* a general floor plan with sprinklers, branches and a sized main
* the same drawing as DXF
* an enlarged plan repeating part of the general plan
* a superseded and a current revision of one sheet
* a not-to-scale sheet
* a legend sheet
* a low-resolution raster version
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFGraphic
from ezdxf.layouts import Modelspace

from firebid.evals.schema import (
    BoqLineTruth,
    InputClass,
    ObjectCount,
    ObjectType,
    PipeLength,
    RevisionStatus,
    SheetTruth,
    TenderTruth,
)

# Layers, named the way a consultant would name them.
LAYER_SPRINKLER = "FP-SPRINKLER"
LAYER_PIPE = "FP-PIPE"
LAYER_TEXT = "FP-TEXT"
LAYER_TITLE = "FP-TITLE"

# A sprinkler is drawn as a circle of this radius, in millimetres of paper at 1:100.
SPRINKLER_RADIUS_MM = 60
# Spacing between heads, in drawing units (millimetres at full size).
SPACING_MM = 3_000


@dataclass(frozen=True)
class GeneratedSheet:
    """One drawing, its truth, and where it was written."""

    truth: SheetTruth
    dxf_path: Path | None = None
    pdf_path: Path | None = None
    png_path: Path | None = None


@dataclass(frozen=True)
class GeneratedTender:
    truth: TenderTruth
    sheets: tuple[GeneratedSheet, ...]


def _new_drawing() -> tuple[Drawing, Modelspace]:
    document = ezdxf.new(dxfversion="R2010", setup=True)
    for name, colour in (
        (LAYER_SPRINKLER, 1),
        (LAYER_PIPE, 3),
        (LAYER_TEXT, 7),
        (LAYER_TITLE, 7),
    ):
        if name not in document.layers:
            document.layers.add(name, color=colour)
    return document, document.modelspace()


# The sheet: an A3 border at 1:100 in drawing units, with the title block in its bottom-right
# corner, where consultants put it. Everything a plan draws sits inside the border.
SHEET_ORIGIN = (-4_000.0, -1_000.0)
SHEET_SIZE = (42_000.0, 29_700.0)
# The border above is drawn at 1:100, so a sheet prints on paper a hundredth of its size: A3.
# A sheet at another scale has its border and title block scaled to match.
DRAWING_SCALE = 100
PAPER_MM = (SHEET_SIZE[0] / DRAWING_SCALE, SHEET_SIZE[1] / DRAWING_SCALE)
TITLE_BLOCK_WIDTH = 14_000.0
CONSULTANT_NAME = "SYNTHETIC CONSULTANTS PTE LTD"
PROJECT_NAME = "PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY"
LABEL_HEIGHT = 150
VALUE_HEIGHT = 260
FIRST_ISSUE = date(2026, 5, 1)


def revision_history(revision: str) -> list[tuple[str, date]]:
    """Every issue up to and including `revision`, oldest first, two weeks apart.

    `R04` has R01 to R04; a revision without a trailing number has only itself. The dates are
    what a transmittal would carry.
    """
    prefix = revision.rstrip("0123456789")
    digits = revision[len(prefix) :]
    if not digits:
        return [(revision, FIRST_ISSUE)]
    width = len(digits)
    return [
        (f"{prefix}{number:0{width}d}", FIRST_ISSUE + timedelta(days=14 * (number - 1)))
        for number in range(1, int(digits) + 1)
    ]


def _text(space: Modelspace, text: str, x: float, y: float, height: float) -> None:
    space.add_text(text, height=height, dxfattribs={"layer": LAYER_TITLE}).set_placement((x, y))


def _box(space: Modelspace, x0: float, y0: float, x1: float, y1: float) -> None:
    space.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": LAYER_TITLE}
    )


def _cell(
    space: Modelspace,
    label: str,
    value: str,
    box: tuple[float, float, float, float],
    value_height: float = VALUE_HEIGHT,
) -> None:
    """A labelled cell: the label small in the top-left corner, the value below it."""
    x0, y0, x1, y1 = box
    _box(space, x0, y0, x1, y1)
    _text(space, label, x0 + 150, y1 - 300, LABEL_HEIGHT)
    _text(space, value, x0 + 150, y0 + 250, value_height)


def _title_block(
    space: Modelspace,
    sheet_number: str,
    revision: str,
    scale: str,
    title: str = "FIRE SPRINKLER LAYOUT",
    drawing_scale: int = DRAWING_SCALE,
) -> None:
    """A sheet border and a title block laid out the way consultants lay them out.

    Labelled cells for drawing number, revision, scale and date; the title and project above;
    and a revision history listing every issue. The history is a trap for a careless reader:
    it holds several revision labels, and only the REV cell says which one this sheet is.
    """
    # Laid out at 1:100, then scaled to the sheet's own scale: a 1:50 sheet's border and
    # title block are half the size in drawing units, so they print the same on paper.
    first = len(space)
    sx, sy = SHEET_ORIGIN
    width, height = SHEET_SIZE
    _box(space, sx, sy, sx + width, sy + height)

    right = sx + width
    left = right - TITLE_BLOCK_WIDTH
    history = revision_history(revision)
    issued = history[-1][1]

    # Bottom row: the four fields an estimator needs.
    row = (sy, sy + 2_000)
    _cell(space, "DRAWING NO.", sheet_number, (left, row[0], left + 7_000, row[1]), 400)
    _cell(space, "REV", revision, (left + 7_000, row[0], left + 9_000, row[1]), 400)
    _cell(space, "SCALE", scale, (left + 9_000, row[0], left + 11_500, row[1]))
    _cell(space, "DATE", issued.strftime("%d.%m.%Y"), (left + 11_500, row[0], right, row[1]))
    _cell(space, "DRAWING TITLE", title, (left, sy + 2_000, right, sy + 3_600))
    _cell(space, "PROJECT", PROJECT_NAME, (left, sy + 3_600, right, sy + 5_000), 220)
    _box(space, left, sy + 5_000, right, sy + 6_000)
    _text(space, CONSULTANT_NAME, left + 150, sy + 5_350, 300)

    # The revision history, above the title block, newest at the top.
    base = sy + 6_000
    line = 400
    top = base + line * (len(history) + 1)
    _box(space, left, base, right, top)
    for column, heading in ((0, "REV"), (2_000, "DATE"), (5_000, "DESCRIPTION")):
        _text(space, heading, left + 150 + column, top - 300, LABEL_HEIGHT)
    for index, (label, when) in enumerate(reversed(history)):
        y = top - line * (index + 2) + 120
        description = "ISSUED FOR TENDER" if index == 0 else "SUPERSEDED ISSUE"
        _text(space, label, left + 150, y, 180)
        _text(space, when.strftime("%d.%m.%Y"), left + 2_150, y, 180)
        _text(space, description, left + 5_150, y, 180)

    if drawing_scale != DRAWING_SCALE:
        from ezdxf.math import Matrix44

        factor = drawing_scale / DRAWING_SCALE
        for entity in list(space)[first:]:
            entity.transform(Matrix44.scale(factor, factor, factor))


# The structural grid: lines every 6 m, offset from the heads so they never coincide with
# pipework, lettered left to right and numbered bottom to top, as Singapore plans are.
GRID_SPACING = 6_000
GRID_ORIGIN = (500.0, 500.0)
LAYER_GRID = "S-GRID"
LAYER_DIMENSION = "FP-DIM"


def grid_lines(columns: int, rows: int) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """The grid a plan of this size is drawn on: (label, x) across and (label, y) up."""
    width = (columns - 1) * SPACING_MM + 3_000
    height = (rows - 1) * SPACING_MM + 3_000
    across = [
        (chr(ord("A") + index), GRID_ORIGIN[0] + index * GRID_SPACING)
        for index in range(int(width // GRID_SPACING) + 1)
    ]
    up = [
        (str(index + 1), GRID_ORIGIN[1] + index * GRID_SPACING)
        for index in range(int(height // GRID_SPACING) + 1)
    ]
    return across, up


def _structural_grid(
    space: Modelspace, columns: int, rows: int, drawing_scale: int = DRAWING_SCALE
) -> None:
    across, up = grid_lines(columns, rows)
    bottom, top = up[0][1] - 1_000, up[-1][1] + 1_000
    left, right = across[0][1] - 1_000, across[-1][1] + 1_000
    # A bubble is 8 mm across on paper whatever the scale, so its size in drawing units is not.
    radius = 4 * drawing_scale
    text = 2.5 * drawing_scale
    attributes = {"layer": LAYER_GRID}
    for label, x in across:
        space.add_line((x, bottom), (x, top), dxfattribs=attributes)
        space.add_circle((x, top + radius), radius, dxfattribs=attributes)
        space.add_text(label, height=text, dxfattribs=attributes).set_placement(
            (x - text * 0.35, top + radius - text / 2)
        )
    for label, y in up:
        space.add_line((left, y), (right, y), dxfattribs=attributes)
        space.add_circle((left - radius, y), radius, dxfattribs=attributes)
        space.add_text(label, height=text, dxfattribs=attributes).set_placement(
            (left - radius - text * 0.35, y - text / 2)
        )


def _dimensions(space: Modelspace) -> None:
    """Head spacing and grid spacing, dimensioned the way a consultant dimensions them."""
    across, _ = grid_lines(8, 6)
    style = {"layer": LAYER_DIMENSION}
    # Sized in drawing units to print as a consultant's do at 1:100: a 2.5 mm figure and
    # arrows. DIMLFAC stays 1, so the figure is the measured length itself.
    paper = DRAWING_SCALE
    printed = {
        "dimtxt": 2.5 * paper,
        "dimasz": 2.5 * paper,
        "dimgap": 0.6 * paper,
        "dimexo": 1.0 * paper,
        "dimexe": 1.0 * paper,
        "dimlfac": 1.0,
        "dimdec": 0,
    }
    space.add_aligned_dim(
        p1=(2_000, 2_000), p2=(5_000, 2_000), distance=-600, dxfattribs=style, override=printed
    ).render()
    space.add_aligned_dim(
        p1=(across[0][1], 16_000),
        p2=(across[1][1], 16_000),
        distance=600,
        dxfattribs=style,
        override=printed,
    ).render()


def _view_title(
    space: Modelspace, title: str, scale: str, drawing_scale: int = DRAWING_SCALE
) -> None:
    """A view's title with its scale beneath, in the top-left of the sheet.

    Placed 45 mm down and 15 mm in on paper at any scale, clear of the plan, the grid bubbles
    and the title block.
    """
    factor = drawing_scale / DRAWING_SCALE
    frame_left = SHEET_ORIGIN[0] * factor
    frame_top = (SHEET_ORIGIN[1] + SHEET_SIZE[1]) * factor
    x, y = frame_left + 15 * drawing_scale, frame_top - 45 * drawing_scale
    size = 3.5 * drawing_scale
    attributes = {"layer": LAYER_TEXT}
    space.add_text(title, height=size, dxfattribs=attributes).set_placement((x, y))
    space.add_text(f"SCALE {scale}", height=size * 0.7, dxfattribs=attributes).set_placement(
        (x, y - 1.8 * size)
    )


def _sprinkler_grid(
    space: Modelspace, columns: int, rows: int, origin: tuple[float, float] = (2_000, 2_000)
) -> int:
    """A regular grid of pendent heads. Returns how many were drawn."""
    drawn = 0
    for column in range(columns):
        for row in range(rows):
            centre = (origin[0] + column * SPACING_MM, origin[1] + row * SPACING_MM)
            space.add_circle(
                centre, radius=SPRINKLER_RADIUS_MM, dxfattribs={"layer": LAYER_SPRINKLER}
            )
            drawn += 1
    return drawn


def _branches(
    space: Modelspace, columns: int, rows: int, origin: tuple[float, float] = (2_000, 2_000)
) -> int:
    """One branch line per row of heads. Returns total length in millimetres."""
    total = 0
    for row in range(rows):
        start = (origin[0], origin[1] + row * SPACING_MM)
        end = (origin[0] + (columns - 1) * SPACING_MM, origin[1] + row * SPACING_MM)
        space.add_line(start, end, dxfattribs={"layer": LAYER_PIPE})
        total += int(end[0] - start[0])
    return total


def _main(space: Modelspace, rows: int, origin: tuple[float, float] = (2_000, 2_000)) -> int:
    """A riser main down the left, annotated with its size. Returns its length."""
    start = (origin[0] - 1_000, origin[1])
    end = (origin[0] - 1_000, origin[1] + (rows - 1) * SPACING_MM)
    space.add_line(start, end, dxfattribs={"layer": LAYER_PIPE})
    space.add_text("DN150 RISING MAIN", height=180, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (start[0] - 2_500, (start[1] + end[1]) / 2)
    )
    return int(end[1] - start[1])


def general_arrangement(
    sheet_number: str = "FP-L05-201",
    revision: str = "R04",
    columns: int = 8,
    rows: int = 6,
    *,
    with_grid: bool = True,
    with_dimensions: bool = True,
    view_title: str | None = "LEVEL 5 FIRE SPRINKLER LAYOUT PLAN",
) -> tuple[Drawing, SheetTruth]:
    """A floor plan: a grid of heads, a branch per row, and a sized main.

    With a structural grid, dimensions and a view title by default, which is what a scale is
    verified from and a grid reference read against. Without them it is a sheet whose stated
    scale nothing on it can confirm.
    """
    document, space = _new_drawing()
    heads = _sprinkler_grid(space, columns, rows)
    branch_mm = _branches(space, columns, rows)
    main_mm = _main(space, rows)
    if with_grid:
        _structural_grid(space, columns, rows)
    if with_dimensions:
        _dimensions(space)
    if view_title:
        _view_title(space, view_title, "1:100")
    _title_block(space, sheet_number, revision, "1:100")

    truth = SheetTruth(
        sheet_number=sheet_number,
        revision=revision,
        status=RevisionStatus.CURRENT,
        input_class=InputClass.DWG,
        counts=(ObjectCount(object_type=ObjectType.SPRINKLER_PENDENT, count=heads),),
        pipe_lengths=(
            PipeLength(nominal_diameter_mm=50, length_mm=branch_mm),
            PipeLength(nominal_diameter_mm=150, length_mm=main_mm),
        ),
    )
    return document, truth


def enlarged_plan(
    sheet_number: str = "FP-L05-301",
    revision: str = "R01",
    source_sheet: str = "FP-L05-201",
    columns: int = 3,
    rows: int = 2,
) -> tuple[Drawing, SheetTruth]:
    """Part of the general arrangement, drawn larger. The double-counting trap.

    Drawn at a true 1:50: the same heads in the same place as on the general arrangement, on
    a sheet half the size in drawing units, titled as an enlarged plan.
    """
    document, space = _new_drawing()
    heads = _sprinkler_grid(space, columns, rows)
    branch_mm = _branches(space, columns, rows)
    _structural_grid(space, columns, rows, drawing_scale=50)
    _view_title(space, "ENLARGED PLAN - RISER AREA", "1:50", drawing_scale=50)
    _title_block(
        space, sheet_number, revision, "1:50", title="ENLARGED SPRINKLER PLAN", drawing_scale=50
    )

    truth = SheetTruth(
        sheet_number=sheet_number,
        revision=revision,
        status=RevisionStatus.CURRENT,
        input_class=InputClass.DWG,
        counts=(ObjectCount(object_type=ObjectType.SPRINKLER_PENDENT, count=heads),),
        pipe_lengths=(PipeLength(nominal_diameter_mm=50, length_mm=branch_mm),),
        duplicates_of=(source_sheet,),
    )
    return document, truth


def not_to_scale_sheet(
    sheet_number: str = "FP-SCH-001", revision: str = "R01"
) -> tuple[Drawing, SheetTruth]:
    """A riser diagram: real objects, no measurable lengths."""
    document, space = _new_drawing()
    for index in range(4):
        space.add_circle(
            (2_000, 2_000 + index * 1_500),
            radius=SPRINKLER_RADIUS_MM,
            dxfattribs={"layer": LAYER_SPRINKLER},
        )
    space.add_text(
        "SCHEMATIC - NOT TO SCALE", height=250, dxfattribs={"layer": LAYER_TEXT}
    ).set_placement((2_000, 10_000))
    _title_block(space, sheet_number, revision, "NTS")

    truth = SheetTruth(
        sheet_number=sheet_number,
        revision=revision,
        status=RevisionStatus.CURRENT,
        input_class=InputClass.DWG,
        not_to_scale=True,
        counts=(ObjectCount(object_type=ObjectType.LANDING_VALVE, count=4),),
    )
    return document, truth


def legend_sheet(
    sheet_number: str = "FP-LEG-001", revision: str = "R01"
) -> tuple[Drawing, SheetTruth]:
    """A legend. Symbols appear, but none of them are installed anywhere.

    The trap this catches is a detector that counts the legend's example symbols as real
    items and adds them to the take-off.
    """
    document, space = _new_drawing()
    for index, label in enumerate(
        ("PENDENT SPRINKLER", "UPRIGHT SPRINKLER", "HOSE REEL", "LANDING VALVE")
    ):
        height = 2_000 + index * 1_200
        space.add_circle(
            (2_000, height), radius=SPRINKLER_RADIUS_MM, dxfattribs={"layer": LAYER_SPRINKLER}
        )
        space.add_text(label, height=180, dxfattribs={"layer": LAYER_TEXT}).set_placement(
            (2_600, height)
        )
    space.add_text("LEGEND", height=300, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (2_000, 8_000)
    )
    _title_block(space, sheet_number, revision, "NTS")

    truth = SheetTruth(
        sheet_number=sheet_number,
        revision=revision,
        status=RevisionStatus.CURRENT,
        input_class=InputClass.DWG,
        not_to_scale=True,
        # Deliberately empty: a legend contains no installed items.
        counts=(),
    )
    return document, truth


def write_dxf(document: Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(path)
    return path


# The border is drawn at 1:100, so the sheet prints on paper a hundredth of its size: A3.


def drawing_scale_of(document: Drawing) -> int:
    """The scale a fixture states (`1:50`), which it is printed at; 1:100 when it states none."""
    from firebid.parsing.dxf import stated_scale

    return stated_scale(document.modelspace()) or DRAWING_SCALE


def _plot(document: Drawing, *, outlines_only: bool = True) -> tuple[Any, Any]:
    """Draw the sheet onto a figure the size of its paper, at its own stated scale.

    Left to itself, ezdxf fits the figure to the drawing, which prints an A3 sheet at a third
    of its size and makes 2.5 mm title-block text less than a millimetre. Pinning the axes to
    the border and the figure to the paper keeps text the size a real sheet prints it.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    factor = drawing_scale_of(document) / DRAWING_SCALE
    figure = plt.figure(figsize=(PAPER_MM[0] / 25.4, PAPER_MM[1] / 25.4))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()

    def drawn(entity: DXFGraphic) -> bool:
        # With live text, text and dimensions are written separately, as real text.
        return outlines_only or entity.dxftype() not in ("TEXT", "MTEXT", "DIMENSION")

    try:
        # Printed on white, the way a tender sheet is. The default background is modelspace's
        # black, which draws colour 7 (the title block's) white and so invisible on paper.
        printed = Configuration(background_policy=BackgroundPolicy.WHITE)
        Frontend(RenderContext(document), MatplotlibBackend(axes), config=printed).draw_layout(
            document.modelspace(), finalize=True, filter_func=drawn
        )
        figure.set_size_inches(PAPER_MM[0] / 25.4, PAPER_MM[1] / 25.4)
        axes.set_aspect("auto")
        axes.set_position((0, 0, 1, 1))
        axes.set_xlim(SHEET_ORIGIN[0] * factor, (SHEET_ORIGIN[0] + SHEET_SIZE[0]) * factor)
        axes.set_ylim(SHEET_ORIGIN[1] * factor, (SHEET_ORIGIN[1] + SHEET_SIZE[1]) * factor)
    except BaseException:
        plt.close(figure)
        raise
    return figure, axes


def write_pdf(document: Drawing, path: Path, *, live_text: bool = False) -> Path:
    """Render to vector PDF on A3 paper at the sheet's scale, as a vector tender looks.

    By default every character is drawn as outlines, as an AutoCAD export with SHX fonts is:
    the page has no text layer and has to be read by OCR. With `live_text`, every piece of
    text (title block, labels, view titles, dimension figures) is written as real text, as a
    TrueType export is, so it can be read straight from the page.
    """
    import matplotlib
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = _plot(document, outlines_only=not live_text)
    try:
        if live_text:
            _draw_live_text(axes, document)
        with matplotlib.rc_context({"pdf.fonttype": 42}):  # embedded TrueType: extractable
            figure.savefig(path, format="pdf")
    finally:
        plt.close(figure)
    return path


def _is_title_text(entity: DXFGraphic) -> bool:
    return entity.dxftype() == "TEXT" and entity.dxf.layer == LAYER_TITLE


def _draw_live_text(axes: Any, document: Drawing) -> None:
    """Write every text as text, and every dimension from its parts, at printed size."""
    points_per_unit = 72 / 25.4 / drawing_scale_of(document)

    def write(
        x: float,
        y: float,
        value: str,
        height: float,
        rotation: float = 0.0,
        anchor: tuple[str, str] = ("left", "baseline"),
    ) -> None:
        # A TEXT height is the capital height; a font size is about 1.4 times that.
        axes.text(
            x,
            y,
            value,
            fontsize=height * points_per_unit * 1.4,
            ha=anchor[0],
            va=anchor[1],
            rotation=rotation,
            rotation_mode="anchor",
        )

    # MTEXT is placed by an attachment point, 1 to 9: top, middle, bottom by left, centre,
    # right. A dimension's figure is attached at its middle-centre.
    attachments = {
        1: ("left", "top"),
        2: ("center", "top"),
        3: ("right", "top"),
        4: ("left", "center"),
        5: ("center", "center"),
        6: ("right", "center"),
        7: ("left", "bottom"),
        8: ("center", "bottom"),
        9: ("right", "bottom"),
    }

    for entity in document.modelspace():
        kind = entity.dxftype()
        if kind == "TEXT":
            x, y, _ = entity.dxf.insert
            write(x, y, entity.dxf.text, entity.dxf.height, entity.dxf.get("rotation", 0.0))
        elif kind == "DIMENSION":
            for part in cast(Any, entity).virtual_entities():
                part_kind = part.dxftype()
                if part_kind == "LINE":
                    axes.plot(
                        [part.dxf.start.x, part.dxf.end.x],
                        [part.dxf.start.y, part.dxf.end.y],
                        color="black",
                        linewidth=0.25,
                    )
                elif part_kind in ("MTEXT", "TEXT"):
                    x, y, _ = part.dxf.insert
                    rotation = part.dxf.get("rotation", 0.0)
                    if part_kind == "MTEXT":
                        anchor = attachments.get(
                            part.dxf.get("attachment_point", 1), ("left", "top")
                        )
                        write(x, y, part.plain_text(), part.dxf.char_height, rotation, anchor)
                    else:
                        write(x, y, part.dxf.text, part.dxf.height, rotation)


def write_raster(document: Drawing, path: Path, dpi: int = 72) -> Path:
    """A scan of the A3 sheet at `dpi`. The default is low, which is where accuracy falls over."""
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, _ = _plot(document)
    try:
        figure.savefig(path, format="png", dpi=dpi)
    finally:
        plt.close(figure)
    return path


def dxf_bytes(document: Drawing) -> bytes:
    stream = io.StringIO()
    document.write(stream)
    return stream.getvalue().encode("utf-8")


def generate_tender(
    out_dir: Path,
    tender_id: str = "SYNTH-001",
    consultant: str = "Synthetic Consultants",
    seed: int = 1,
    *,
    with_raster: bool = True,
) -> GeneratedTender:
    """Every fixture type, written under `out_dir`, with the truth that goes with them."""
    rng = random.Random(seed)  # noqa: S311  # reproducible fixtures, not cryptography
    # The seed varies the plan's size, so a predictor cannot be tuned to one shape.
    columns = rng.choice([6, 7, 8])
    rows = rng.choice([4, 5, 6])

    generated: list[GeneratedSheet] = []
    truths: list[SheetTruth] = []

    # The general arrangement, as DXF and as vector PDF: the same drawing, two input classes.
    ga_document, ga_truth = general_arrangement(columns=columns, rows=rows)
    ga_dxf = write_dxf(ga_document, out_dir / f"{tender_id}-FP-L05-201.dxf")
    ga_pdf = write_pdf(ga_document, out_dir / f"{tender_id}-FP-L05-201.pdf")
    generated.append(GeneratedSheet(truth=ga_truth, dxf_path=ga_dxf, pdf_path=ga_pdf))
    truths.append(ga_truth)

    # The revision it replaced: same number, earlier revision, superseded.
    old_document, old_truth = general_arrangement(revision="R03", columns=columns, rows=rows)
    superseded = old_truth.model_copy(update={"status": RevisionStatus.SUPERSEDED})
    old_dxf = write_dxf(old_document, out_dir / f"{tender_id}-FP-L05-201-R03.dxf")
    generated.append(GeneratedSheet(truth=superseded, dxf_path=old_dxf))
    truths.append(superseded)

    enlarged_document, enlarged_truth = enlarged_plan()
    enlarged_dxf = write_dxf(enlarged_document, out_dir / f"{tender_id}-FP-L05-301.dxf")
    generated.append(GeneratedSheet(truth=enlarged_truth, dxf_path=enlarged_dxf))
    truths.append(enlarged_truth)

    nts_document, nts_truth = not_to_scale_sheet()
    nts_dxf = write_dxf(nts_document, out_dir / f"{tender_id}-FP-SCH-001.dxf")
    generated.append(GeneratedSheet(truth=nts_truth, dxf_path=nts_dxf))
    truths.append(nts_truth)

    legend_document, legend_truth = legend_sheet()
    legend_dxf = write_dxf(legend_document, out_dir / f"{tender_id}-FP-LEG-001.dxf")
    generated.append(GeneratedSheet(truth=legend_truth, dxf_path=legend_dxf))
    truths.append(legend_truth)

    if with_raster:
        raster_document, raster_truth = general_arrangement(
            sheet_number="FP-L06-201", columns=columns, rows=rows
        )
        scanned = raster_truth.model_copy(update={"input_class": InputClass.RASTER})
        png = write_raster(raster_document, out_dir / f"{tender_id}-FP-L06-201.png")
        generated.append(GeneratedSheet(truth=scanned, png_path=png))
        truths.append(scanned)

    total_heads = sum(
        sheet.count_of(ObjectType.SPRINKLER_PENDENT)
        for sheet in truths
        if sheet.status is RevisionStatus.CURRENT
    )
    tender = TenderTruth(
        tender_id=tender_id,
        consultant=consultant,
        input_class=InputClass.MIXED,
        sheets=tuple(truths),
        boq_lines=(
            BoqLineTruth(
                line_reference="2.1",
                description="Pendent sprinkler heads",
                unit="nr",
                quantity=float(total_heads),
                maps_to=ObjectType.SPRINKLER_PENDENT,
            ),
            BoqLineTruth(
                line_reference="1.1",
                description="Preliminaries",
                unit="item",
                quantity=1.0,
                maps_to=None,
            ),
        ),
        notes=f"Synthetic, seed {seed}. Not a substitute for the golden set (decision D3).",
    )
    return GeneratedTender(truth=tender, sheets=tuple(generated))

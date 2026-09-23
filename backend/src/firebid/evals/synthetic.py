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
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
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


def _title_block(space: Modelspace, sheet_number: str, revision: str, scale: str) -> None:
    """A minimal title block, so title-block reading has something to read."""
    for index, text in enumerate((f"SHEET: {sheet_number}", f"REV: {revision}", f"SCALE: {scale}")):
        space.add_text(
            text,
            height=200,
            dxfattribs={"layer": LAYER_TITLE},
        ).set_placement((30_000, 1_000 + index * 400))


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
) -> tuple[Drawing, SheetTruth]:
    """A floor plan: a grid of heads, a branch per row, and a sized main."""
    document, space = _new_drawing()
    heads = _sprinkler_grid(space, columns, rows)
    branch_mm = _branches(space, columns, rows)
    main_mm = _main(space, rows)
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
    """Part of the general arrangement, drawn larger. The double-counting trap."""
    document, space = _new_drawing()
    heads = _sprinkler_grid(space, columns, rows)
    branch_mm = _branches(space, columns, rows)
    _title_block(space, sheet_number, revision, "1:50")

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


def write_pdf(document: Drawing, path: Path) -> Path:
    """Render to vector PDF through matplotlib, which is what a vector tender looks like."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    path.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(16.5, 11.7))  # A3 landscape, in inches
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()
    try:
        from ezdxf.addons.drawing import Frontend

        Frontend(RenderContext(document), MatplotlibBackend(axes)).draw_layout(
            document.modelspace(), finalize=True
        )
        figure.savefig(path, format="pdf")
    finally:
        plt.close(figure)
    return path


def write_raster(document: Drawing, path: Path, dpi: int = 72) -> Path:
    """A low-resolution scan, which is where accuracy usually falls over."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    path.parent.mkdir(parents=True, exist_ok=True)
    figure = plt.figure(figsize=(16.5, 11.7))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()
    try:
        Frontend(RenderContext(document), MatplotlibBackend(axes)).draw_layout(
            document.modelspace(), finalize=True
        )
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

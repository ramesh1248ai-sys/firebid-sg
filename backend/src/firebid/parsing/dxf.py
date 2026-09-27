"""Reading a DXF: which layouts it has, how big they are, and how to draw them.

Like the PDF reader, every function here runs inside the sandbox and returns plain data.

**A DXF is not paper.** A PDF page has a size; a DXF has drawing units and, if the
consultant set one up, a paper size on each paperspace layout. Fire-protection drawings are
drawn in millimetres at 1:1 in modelspace and plotted from a paperspace layout, so:

* a paperspace layout with a page setup gives a real sheet size, and it is used;
* a layout with no page setup falls back to its content's extents, which is the honest
  answer even when it is an awkward size;
* a file with nothing in paperspace is treated as one sheet of modelspace, because that is
  what a consultant sending a bare model expects to see.

DWG is not read here. Until ADR-003 records a converter licence, a DWG is converted to DXF
before it reaches this module, or refused at ingest with a message asking for the DXF.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from typing import Any

# Anything below this is a rounding error in a layout's extents, not a drawing.
MIN_SHEET_MM = 10.0
# An A0 sheet is 1189 mm. A "layout" larger than this is modelspace extents in metres or a
# stray entity a long way from the origin, so it is clamped and recorded as suspect.
MAX_SHEET_MM = 5_000.0
# What a layout with no usable size falls back to: A1 landscape, the commonest tender sheet.
FALLBACK_SIZE_MM = (841.0, 594.0)


class DxfUnreadable(Exception):
    """The file is not a DXF we can open. Carries a reason an estimator can act on."""


@dataclass(frozen=True)
class LayoutFacts:
    """One layout, as the ingest pipeline needs it."""

    index: int
    width_mm: float
    height_mm: float
    content_class: str
    label: str
    space: str  # paper | model
    entity_count: int
    sized_from: str  # page_setup | stated_scale | extents | fallback
    extents_mm: list[float] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(payload: bytes) -> Any:
    """Open a DXF, repairing what can be repaired, and say plainly when it cannot."""
    import ezdxf
    from ezdxf import recover

    try:
        document, auditor = recover.read(io.BytesIO(payload))
    except (OSError, ezdxf.DXFError, UnicodeDecodeError) as error:
        raise DxfUnreadable(
            f"this DXF could not be opened, which usually means it is damaged or is not "
            f"actually a DXF ({error})"
        ) from error

    if auditor.has_errors:
        # Recovered, but with losses worth recording: the estimator should know the drawing
        # they are measuring was not read cleanly.
        first = auditor.errors[0]
        document.firebid_audit_note = f"{len(auditor.errors)} errors, first: {first}"  # type: ignore[attr-defined]
    return document


def _layout_size(layout: Any) -> tuple[float, float, str, list[float] | None]:
    """A layout's sheet size in millimetres, and where the number came from."""
    from ezdxf import bbox

    paper_width = float(getattr(layout.dxf, "paper_width", 0.0) or 0.0)
    paper_height = float(getattr(layout.dxf, "paper_height", 0.0) or 0.0)
    if _plausible(paper_width) and _plausible(paper_height):
        return paper_width, paper_height, "page_setup", None

    try:
        extents = bbox.extents(layout, fast=True)
    except Exception:
        extents = None

    if extents is not None and extents.has_data:
        width = float(extents.size.x)
        height = float(extents.size.y)
        # Modelspace is drawn full size. When the drawing states the scale it is plotted at,
        # that puts it on paper exactly as its PDF export would be, so the two formats of one
        # drawing share their sheet coordinates.
        denominator = stated_scale(layout) if layout.name.lower() == "model" else None
        if denominator and _plausible(width / denominator) and _plausible(height / denominator):
            width, height = width / denominator, height / denominator
            box = [
                float(extents.extmin.x),
                float(extents.extmin.y),
                float(extents.extmax.x),
                float(extents.extmax.y),
            ]
            return width, height, "stated_scale", box
        box = [
            float(extents.extmin.x),
            float(extents.extmin.y),
            float(extents.extmax.x),
            float(extents.extmax.y),
        ]
        if _plausible(width) and _plausible(height):
            return width, height, "extents", box
        if width > MAX_SHEET_MM or height > MAX_SHEET_MM:
            # A drawing in metres, or one stray entity far from the rest. Keep the
            # proportions and record that the size is not to be trusted.
            longest = max(width, height, 1.0)
            shrink = MAX_SHEET_MM / longest
            return width * shrink, height * shrink, "extents", box

    return FALLBACK_SIZE_MM[0], FALLBACK_SIZE_MM[1], "fallback", None


SCALE_TEXT = re.compile(r"^\s*1\s*:\s*(\d{1,5})(?:\s*@\s*A\d)?\s*$", re.I)


def stated_scale(layout: Any) -> int | None:
    """The plot scale a layout's own text states (`1:100`), as its denominator, if any.

    The commonest one wins: a sheet whose title block says 1:100 may carry a 1:20 detail.
    """
    found: dict[int, int] = {}
    for entity in layout.query("TEXT MTEXT"):
        text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.plain_text()
        match = SCALE_TEXT.match(text or "")
        if match and int(match.group(1)) > 0:
            denominator = int(match.group(1))
            found[denominator] = found.get(denominator, 0) + 1
    return max(found, key=lambda key: found[key]) if found else None


def _plausible(value: float) -> bool:
    return MIN_SHEET_MM <= value <= MAX_SHEET_MM


def _layouts(document: Any) -> list[Any]:
    """Paperspace layouts if there are any with content, otherwise modelspace."""
    paperspaces = []
    for name in document.layouts.names_in_taborder():
        if name.lower() == "model":
            continue
        layout = document.layouts.get(name)
        if len(layout) > 0:
            paperspaces.append(layout)
    return paperspaces or [document.modelspace()]


def inspect_dxf(payload: bytes) -> list[dict[str, Any]]:
    """Every layout's facts. Runs in the sandbox, so it returns plain dicts."""
    document = _read(payload)
    facts: list[dict[str, Any]] = []

    for index, layout in enumerate(_layouts(document)):
        width_mm, height_mm, sized_from, extents = _layout_size(layout)
        is_model = layout.name.lower() == "model"
        facts.append(
            LayoutFacts(
                index=index,
                width_mm=round(width_mm, 2),
                height_mm=round(height_mm, 2),
                # A DXF is geometry by definition: every entity carries real coordinates.
                content_class="vector",
                label=layout.name,
                space="model" if is_model else "paper",
                entity_count=len(layout),
                sized_from=sized_from,
                extents_mm=extents,
            ).as_dict()
        )

    if not facts:
        raise DxfUnreadable("this DXF has no layouts and no drawing in modelspace")
    return facts


def render_layout(payload: bytes, index: int, width_px: int, height_px: int) -> dict[str, Any]:
    """Plot one layout to raw RGB pixels, at the size asked for.

    Drawn through ezdxf's matplotlib backend, which is the same renderer the synthetic
    fixtures use, so what an estimator sees matches what the evaluation measures.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from PIL import Image

    document = _read(payload)
    layouts = _layouts(document)
    if not 0 <= index < len(layouts):
        raise DxfUnreadable(f"this DXF has no layout {index + 1}")
    layout = layouts[index]

    dpi = 100
    figure = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()
    figure.patch.set_facecolor("white")
    buffer = io.BytesIO()
    try:
        Frontend(RenderContext(document), MatplotlibBackend(axes)).draw_layout(
            layout, finalize=True
        )
        figure.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    except Exception as error:
        raise DxfUnreadable(f"this layout could not be drawn ({type(error).__name__})") from error
    finally:
        plt.close(figure)

    buffer.seek(0)
    with Image.open(buffer) as rendered:
        image = rendered.convert("RGB")
        if image.size != (width_px, height_px):
            image = image.resize((width_px, height_px), Image.Resampling.LANCZOS)
        return {"width": image.width, "height": image.height, "pixels": image.tobytes()}

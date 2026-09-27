"""Reading a PDF: what pages it has, how big they are, and what they are made of.

Every function here runs inside the sandbox. They take bytes and return plain data, because
the result crosses a process boundary, and they never touch the database or object storage.

**Why the content class matters.** A vector PDF exported from CAD carries the geometry: a
pipe is a path with real coordinates, and measuring it is arithmetic. A scan of a printed
drawing carries pixels, and measuring it needs detection, which is slower and less certain.
The same tender set routinely contains both, so each page is classified at ingest and the
later stages branch on it rather than discovering the problem one sheet at a time.

A page's size comes from its **crop box**, not its media box: that is the area a viewer shows
and a printer prints, and consultants regularly export A1 content inside an A0 media box.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# PDF user-space units are 1/72 inch, whatever the page claims elsewhere.
POINTS_PER_MM = 72.0 / 25.4

# A page whose images cover this much of it is a scan, whatever else is on it. The margin is
# generous because a scan's image rarely reaches the paper edge.
RASTER_COVERAGE = 0.80
# Below this, images are an inserted logo or a detail photograph on a vector drawing.
VECTOR_COVERAGE = 0.10
# A vector page has real geometry on it. Fewer than this and it is a cover sheet or blank.
MIN_VECTOR_OBJECTS = 8


class PdfUnreadable(Exception):
    """The file is not a PDF we can open. Carries a reason an estimator can act on."""


@dataclass(frozen=True)
class PageFacts:
    """One page, as the ingest pipeline needs it. Crosses the sandbox boundary, so plain."""

    index: int
    width_mm: float
    height_mm: float
    rotation: int
    content_class: str  # vector | raster | mixed | empty
    path_objects: int
    text_objects: int
    image_objects: int
    other_objects: int
    image_coverage: float
    label: str
    # The resolution of the largest image on the page, in pixels per inch of paper: what a
    # scan's legibility mostly comes down to (FR-DOC-06). None when the page has no image.
    image_dpi: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _open(payload: bytes) -> Any:
    """Open a PDF, turning every way that fails into a sentence."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(payload)
        # Page count is the first call that actually parses the structure, so a truncated
        # file fails here rather than at an unpredictable point later.
        len(document)
    except pdfium.PdfiumError as error:
        message = str(error).lower()
        if "password" in message:
            raise PdfUnreadable(
                "this PDF is password-protected; send an unprotected copy and it will be read"
            ) from error
        raise PdfUnreadable(
            f"this PDF could not be opened, which usually means it is damaged or incomplete "
            f"({error})"
        ) from error
    return document


def classify(
    path_objects: int, text_objects: int, image_objects: int, image_coverage: float
) -> str:
    """Vector, raster, mixed or empty, from what the page is made of.

    The order matters. Coverage decides first, because a scan with a vector title block
    stamped on it is still a scan as far as measuring is concerned.
    """
    vector_objects = path_objects + text_objects

    if image_objects == 0 and vector_objects == 0:
        return "empty"
    if image_coverage >= RASTER_COVERAGE:
        return "raster"
    if image_objects == 0 or image_coverage < VECTOR_COVERAGE:
        return "vector" if vector_objects >= MIN_VECTOR_OBJECTS else "empty"
    return "mixed"


def _page_facts(document: Any, index: int) -> PageFacts:
    import pypdfium2.raw as raw

    page = document[index]
    # The crop box is what is shown and printed; the media box can be much larger.
    try:
        left, bottom, right, top = page.get_cropbox()
    except Exception:
        # A page with no crop box: its declared size is the next best answer.
        width_points, height_points = page.get_size()
        left, bottom, right, top = 0.0, 0.0, width_points, height_points

    width_points = abs(right - left)
    height_points = abs(top - bottom)
    rotation = page.get_rotation()
    if rotation in (90, 270):
        width_points, height_points = height_points, width_points

    page_area = max(width_points * height_points, 1.0)
    counts = {"path": 0, "text": 0, "image": 0, "other": 0}
    image_area = 0.0
    largest_area = 0.0
    image_dpi: float | None = None

    for obj in page.get_objects():
        if obj.type == raw.FPDF_PAGEOBJ_PATH:
            counts["path"] += 1
        elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
            counts["text"] += 1
        elif obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            counts["image"] += 1
            try:
                obj_left, obj_bottom, obj_right, obj_top = obj.get_bounds()
                width, height = abs(obj_right - obj_left), abs(obj_top - obj_bottom)
                image_area += width * height
                if width * height > largest_area and width > 0:
                    largest_area = width * height
                    pixels_wide, _ = obj.get_px_size()
                    image_dpi = round(pixels_wide / (width / 72), 1)
            except Exception:  # noqa: S110 - an image with no usable bounds counts as none
                pass
        else:
            counts["other"] += 1

    # Overlapping images can total more than the page; coverage is a proportion, so cap it.
    coverage = min(image_area / page_area, 1.0)

    return PageFacts(
        index=index,
        width_mm=round(width_points / POINTS_PER_MM, 2),
        height_mm=round(height_points / POINTS_PER_MM, 2),
        rotation=rotation,
        content_class=classify(counts["path"], counts["text"], counts["image"], coverage),
        path_objects=counts["path"],
        text_objects=counts["text"],
        image_objects=counts["image"],
        other_objects=counts["other"],
        image_coverage=round(coverage, 4),
        label=f"page {index + 1}",
        image_dpi=image_dpi,
    )


def inspect_pdf(payload: bytes) -> list[dict[str, Any]]:
    """Every page's facts. Runs in the sandbox, so it returns plain dicts."""
    document = _open(payload)
    try:
        if len(document) == 0:
            raise PdfUnreadable("this PDF has no pages")
        return [_page_facts(document, index).as_dict() for index in range(len(document))]
    finally:
        document.close()


def render_page(payload: bytes, index: int, width_px: int, height_px: int) -> dict[str, Any]:
    """Render one page to raw RGB pixels at the size asked for.

    Raw pixels rather than an encoded image: the caller cuts tiles from it and encodes each
    one, so encoding here would be work thrown away.
    """
    document = _open(payload)
    try:
        if not 0 <= index < len(document):
            raise PdfUnreadable(f"this PDF has no page {index + 1}")
        page = document[index]
        width_points, _ = page.get_size()
        # PDFium renders by scale factor, so ask for the one that lands on the width wanted.
        scale = width_px / max(width_points, 1.0)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        if image.size != (width_px, height_px):
            from PIL import Image

            image = image.resize((width_px, height_px), Image.Resampling.LANCZOS)
        return {"width": image.width, "height": image.height, "pixels": image.tobytes()}
    finally:
        document.close()

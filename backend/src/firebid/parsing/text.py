"""Text with positions, for reading title blocks. Runs in the sandbox, returns plain data.

Each function returns ``{"page": [x0, y0, x1, y1], "spans": [[text, x0, y0, x1, y1, conf]]}``
with y growing downwards, whatever the source's own convention. PDF coordinates are paper
millimetres; DXF coordinates are drawing units, which is fine because the title block reader
measures everything relative to text height and page size.

A page with no text layer returns no spans rather than failing: most AutoCAD exports draw
their text as outlines, and that page is read by OCR instead.
"""

from __future__ import annotations

import io
from typing import Any

from firebid.parsing.pdf import PdfUnreadable, _open

POINTS_TO_MM = 25.4 / 72
# Resolution for OCR. 300 dpi is where Tesseract reads 2 mm title-block text reliably.
OCR_DPI = 300
# The most pixels one OCR pass renders. An A0 sheet at 300 dpi is 140 million pixels; the
# corner a title block sits in is about a fifth of that, and this keeps it bounded if not.
OCR_MAX_PIXELS = 40_000_000
# Words Tesseract is less sure of than this are dropped as noise (its scale is 0 to 100).
OCR_MIN_WORD_CONFIDENCE = 30


def pdf_text(payload: bytes, index: int) -> dict[str, Any]:
    """Every run of text on one PDF page, as PDFium segments it."""
    document = _open(payload)
    try:
        if not 0 <= index < len(document):
            raise PdfUnreadable(f"this PDF has no page {index + 1}")
        page = document[index]
        width, height = page.get_size()
        textpage = page.get_textpage()
        spans = []
        for rect in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(rect)
            text = textpage.get_text_bounded(left, bottom, right, top).strip()
            if text:
                spans.append(
                    [
                        text,
                        left * POINTS_TO_MM,
                        (height - top) * POINTS_TO_MM,
                        right * POINTS_TO_MM,
                        (height - bottom) * POINTS_TO_MM,
                        1.0,
                    ]
                )
        return {"page": [0.0, 0.0, width * POINTS_TO_MM, height * POINTS_TO_MM], "spans": spans}
    finally:
        document.close()


def dxf_text(payload: bytes, layout_name: str | None) -> dict[str, Any]:
    """Every TEXT and MTEXT in one layout: paperspace if it has one, else modelspace.

    Title blocks usually live in paperspace, over a viewport onto modelspace, so a layout's
    own entities are the ones to read.
    """
    from ezdxf import bbox

    from firebid.parsing.dxf import _read

    document = _read(payload)
    layout = (
        document.layouts.get(layout_name)
        if layout_name and layout_name in document.layouts.names()
        else document.modelspace()
    )
    extents = bbox.extents(layout, fast=True)
    if not extents.has_data:
        return {"page": [0.0, 0.0, 1.0, 1.0], "spans": []}
    min_x, min_y = extents.extmin.x, extents.extmin.y
    max_x, max_y = extents.extmax.x, extents.extmax.y

    spans = []
    for entity in layout.query("TEXT MTEXT"):
        if entity.dxftype() == "TEXT":
            lines = [entity.dxf.text]
            height = float(entity.dxf.height)
        else:
            lines = entity.plain_text().splitlines() or [""]
            height = float(entity.dxf.char_height)
        x, y, _ = entity.dxf.insert
        for number, line in enumerate(lines):
            if not line.strip():
                continue
            # A DXF gives the baseline and the capital height, not a box; estimate the box
            # the way a proportional font would fill it.
            baseline = y - number * height * 1.6
            spans.append(
                [
                    line.strip(),
                    x,
                    max_y - (baseline + height),
                    x + 0.75 * height * len(line),
                    max_y - baseline,
                    1.0,
                ]
            )
    return {"page": [min_x, 0.0, max_x, max_y - min_y], "spans": spans}


def ocr_text(
    payload: bytes, kind: str, index: int, region: list[float], dpi: int = OCR_DPI
) -> dict[str, Any]:
    """OCR one region of a page, given as fractions of it: ``[x0, y0, x1, y1]``.

    For a PDF the region is rendered at `dpi` and coordinates come back in paper millimetres,
    like `pdf_text`. For a scanned image the pixels are the page and coordinates are pixels.
    The mean word confidence comes back too, as the legibility measure FR-DOC-06 asks for.
    """
    import pytesseract

    image, page, scale = _region_image(payload, kind, index, region, dpi)
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config="--psm 11")

    lines: dict[tuple[int, int, int], list[int]] = {}
    confidences = []
    for word, text in enumerate(data["text"]):
        confidence = float(data["conf"][word])
        if not text.strip() or confidence < OCR_MIN_WORD_CONFIDENCE:
            continue
        confidences.append(confidence)
        key = (data["block_num"][word], data["par_num"][word], data["line_num"][word])
        lines.setdefault(key, []).append(word)

    left_offset = region[0] * (page[2] - page[0])
    top_offset = region[1] * (page[3] - page[1])
    spans = []
    for words in lines.values():
        x0 = min(data["left"][word] for word in words)
        y0 = min(data["top"][word] for word in words)
        x1 = max(data["left"][word] + data["width"][word] for word in words)
        y1 = max(data["top"][word] + data["height"][word] for word in words)
        spans.append(
            [
                " ".join(data["text"][word] for word in words),
                left_offset + x0 * scale,
                top_offset + y0 * scale,
                left_offset + x1 * scale,
                top_offset + y1 * scale,
                min(float(data["conf"][word]) for word in words) / 100,
            ]
        )
    mean = sum(confidences) / len(confidences) / 100 if confidences else 0.0
    return {"page": page, "spans": spans, "mean_confidence": mean, "words": len(confidences)}


def _region_image(
    payload: bytes, kind: str, index: int, region: list[float], dpi: int
) -> tuple[Any, list[float], float]:
    """The region as an image, the page box, and page units per pixel."""
    from PIL import Image

    if kind == "pdf":
        document = _open(payload)
        try:
            if not 0 <= index < len(document):
                raise PdfUnreadable(f"this PDF has no page {index + 1}")
            page = document[index]
            width_pt, height_pt = page.get_size()
            wanted = (region[2] - region[0]) * (region[3] - region[1]) * width_pt * height_pt
            scale = min(dpi / 72, (OCR_MAX_PIXELS / max(wanted, 1.0)) ** 0.5)
            crop = (
                region[0] * width_pt,
                (1 - region[3]) * height_pt,
                (1 - region[2]) * width_pt,
                region[1] * height_pt,
            )
            image = page.render(scale=scale, crop=crop).to_pil().convert("L")
            page_mm = [0.0, 0.0, width_pt * POINTS_TO_MM, height_pt * POINTS_TO_MM]
            return image, page_mm, POINTS_TO_MM / scale
        finally:
            document.close()

    with Image.open(io.BytesIO(payload)) as scan:
        if getattr(scan, "n_frames", 1) > index:
            scan.seek(index)
        width, height = scan.size
        box = (
            round(region[0] * width),
            round(region[1] * height),
            round(region[2] * width),
            round(region[3] * height),
        )
        image = scan.crop(box).convert("L")
    return image, [0.0, 0.0, float(width), float(height)], 1.0

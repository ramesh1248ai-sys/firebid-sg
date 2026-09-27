"""Vector geometry from a PDF page. Runs in the sandbox; returns Parquet bytes.

The primary engine is PDFium through pypdfium2 (ADR-002). It walks the page's objects,
recursing into form XObjects and composing each level's matrix, because a nested object's
points are in its form's space and a drawing assembled from blocks is nested throughout.
Path segments are read with their stroke colour and width, and each path object keeps its
own group, since that grouping is what a symbol is made of.

When PDFium cannot parse a page, pdfplumber reads it instead and every primitive says so
(`pdf_fallback`), so nothing downstream mistakes a fallback reading for a primary one.
"""

from __future__ import annotations

import ctypes
import math
from typing import Any

from firebid.drawings.geometry import Builder, Method, bezier_points, to_parquet
from firebid.parsing.pdf import PdfUnreadable, _open

POINTS_TO_MM = 25.4 / 72
# A page this much covered by images, with no text layer, is a scan: its text is OCR'd.
SCAN_COVERAGE = 0.5
# OCR'd in quadrants, each within the OCR pixel cap, so an A0 scan never renders whole.
OCR_TILES = ((0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 1.0, 0.5), (0.0, 0.5, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0))
# Nesting deeper than this is either a pathological file or an attack; stop descending.
MAX_FORM_DEPTH = 12

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose(inner: Matrix, outer: Matrix) -> Matrix:
    """`inner` applied first, then `outer`: the PDF convention for nested forms."""
    a1, b1, c1, d1, e1, f1 = inner
    a2, b2, c2, d2, e2, f2 = outer
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def extract(payload: bytes, index: int) -> dict[str, Any]:
    """Geometry of one page, as Parquet, with how it was obtained."""
    try:
        parquet, note = _pdfium(payload, index)
        result: dict[str, Any] = {"parquet": parquet, "method": str(Method.PDF_VECTOR)}
        if note:
            result["ocr_note"] = note
        return result
    except PdfUnreadable:
        raise
    except Exception as failure:
        return {
            "parquet": _pdfplumber(payload, index),
            "method": str(Method.PDF_FALLBACK),
            "primary_failure": f"{type(failure).__name__}: {failure}"[:300],
        }


def _pdfium(payload: bytes, index: int) -> tuple[bytes, str | None]:
    import pypdfium2.raw as raw

    document = _open(payload)
    try:
        if not 0 <= index < len(document):
            raise PdfUnreadable(f"this PDF has no page {index + 1}")
        page = document[index]
        left, bottom, right, top = page.get_cropbox()
        builder = Builder(Method.PDF_VECTOR)

        def to_sheet(x: float, y: float, matrix: Matrix) -> tuple[float, float]:
            a, b, c, d, e, f = matrix
            page_x, page_y = a * x + c * y + e, b * x + d * y + f
            return (page_x - left) * POINTS_TO_MM, (top - page_y) * POINTS_TO_MM

        def walk(handle: Any, count: int, get: Any, parent: Matrix, depth: int) -> None:
            for position in range(count):
                obj = get(handle, position)
                kind = raw.FPDFPageObj_GetType(obj)
                matrix = _matrix(raw, obj)
                if kind == raw.FPDF_PAGEOBJ_FORM:
                    if depth < MAX_FORM_DEPTH:
                        walk(
                            obj,
                            raw.FPDFFormObj_CountObjects(obj),
                            raw.FPDFFormObj_GetObject,
                            _compose(matrix, parent),
                            depth + 1,
                        )
                elif kind == raw.FPDF_PAGEOBJ_PATH:
                    _path(raw, obj, _compose(matrix, parent), builder, to_sheet)
                elif kind == raw.FPDF_PAGEOBJ_TEXT:
                    _text_object(raw, obj, textpage, matrix, parent, builder, to_sheet)
                elif kind == raw.FPDF_PAGEOBJ_IMAGE and depth == 0:
                    image_area[0] += _area(raw, obj)

        textpage = page.get_textpage()
        image_area = [0.0]
        walk(page.raw, raw.FPDFPage_CountObjects(page.raw), raw.FPDFPage_GetObject, IDENTITY, 0)
        page_area = max(abs(right - left) * abs(top - bottom), 1.0)
        note = None
        has_text = "text" in builder.columns["kind"]
        if not has_text and image_area[0] / page_area >= SCAN_COVERAGE:
            note = _ocr_words(payload, index, builder)
        return to_parquet(builder.table()), note
    finally:
        document.close()


def _area(raw: Any, obj: Any) -> float:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    if not raw.FPDFPageObj_GetBounds(
        obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)
    ):
        return 0.0
    return abs(right.value - left.value) * abs(top.value - bottom.value)


def _ocr_words(payload: bytes, index: int, builder: Builder) -> str | None:
    """A scan's text, word by word, each with Tesseract's confidence (FR-VIS-06).

    A word straddling two quadrants is read in both; the second reading is dropped when its
    box overlaps one already kept.
    """
    from firebid.parsing.text import ocr_text

    kept: list[tuple[float, float, float, float]] = []
    try:
        for tile in OCR_TILES:
            data = ocr_text(payload, "pdf", index, list(tile), by_word=True)
            for text, x0, y0, x1, y1, confidence in data["spans"]:
                if any(x0 < b[2] and x1 > b[0] and y0 < b[3] and y1 > b[1] for b in kept):
                    continue
                kept.append((x0, y0, x1, y1))
                builder.text(
                    text,
                    (x0, y0, x1, y1),
                    builder.group(),
                    height=y1 - y0,
                    confidence=round(confidence, 3),
                    method=Method.OCR,
                )
    except Exception as failure:
        return f"the scan's text could not be read: {type(failure).__name__}: {failure}"[:300]
    return None


def _matrix(raw: Any, obj: Any) -> Matrix:
    matrix = raw.FS_MATRIX()
    if not raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix)):
        return IDENTITY
    return (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)


def _path(raw: Any, obj: Any, matrix: Matrix, builder: Builder, to_sheet: Any) -> None:
    fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
    raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill_mode), ctypes.byref(stroke))
    style = _style(raw, obj, matrix)
    group = builder.group()
    count = raw.FPDFPath_CountSegments(obj)
    x, y = ctypes.c_float(), ctypes.c_float()

    current: list[float] = []
    start: tuple[float, float] | None = None
    pending: list[tuple[float, float]] = []  # Bézier control points collected so far

    def flush(closed: bool) -> None:
        nonlocal current
        # A path that ends where it began is closed, whether or not it says so: matplotlib and
        # many CAD exporters draw circles that way.
        if (
            len(current) >= 6
            and math.isclose(current[0], current[-2], abs_tol=1e-6)
            and (math.isclose(current[1], current[-1], abs_tol=1e-6))
        ):
            closed = True
        if len(current) >= 4:
            if not stroke.value and fill_mode.value:
                builder.hatch(current, group, **style)  # a fill with no outline
            elif len(current) == 4 and not closed:
                builder.line(current[0], current[1], current[2], current[3], group, **style)
            else:
                builder.polyline(current, group, closed=closed, **style)
        current = []

    for position in range(count):
        segment = raw.FPDFPath_GetPathSegment(obj, position)
        raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y))
        point = to_sheet(x.value, y.value, matrix)
        segment_type = raw.FPDFPathSegment_GetType(segment)
        if segment_type == raw.FPDF_SEGMENT_MOVETO:
            flush(closed=False)
            current = [point[0], point[1]]
            start = point
            pending = []
        elif segment_type == raw.FPDF_SEGMENT_LINETO:
            current += [point[0], point[1]]
        elif segment_type == raw.FPDF_SEGMENT_BEZIERTO:
            pending.append(point)
            if len(pending) == 3 and len(current) >= 2:
                current += bezier_points(
                    (current[-2], current[-1]), pending[0], pending[1], pending[2]
                )
                pending = []
        if raw.FPDFPathSegment_GetClose(segment):
            if start is not None and current[-2:] != [start[0], start[1]]:
                current += [start[0], start[1]]
            flush(closed=True)
    flush(closed=False)


def _style(raw: Any, obj: Any, matrix: Matrix) -> dict[str, Any]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    color = -1
    if raw.FPDFPageObj_GetStrokeColor(
        obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)
    ):
        color = (r.value << 16) | (g.value << 8) | b.value
    width = ctypes.c_float()
    lineweight = None
    if raw.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width)):
        # A width is in the object's space; the matrix's scale carries it to the page.
        scale = math.sqrt(abs(matrix[0] * matrix[3] - matrix[1] * matrix[2])) or 1.0
        lineweight = round(width.value * scale * POINTS_TO_MM, 4)
    return {"color": color, "lineweight": lineweight}


def _text_object(
    raw: Any,
    obj: Any,
    textpage: Any,
    matrix: Matrix,
    parent: Matrix,
    builder: Builder,
    to_sheet: Any,
) -> None:
    """One text object as one span, as a DXF TEXT entity is.

    Not the text page's rectangles: those merge separate objects that happen to share a line,
    so a head label and the branch label beside it became one string ("SP0300 DN4"), and
    the benchmark found 3-5% of labels lost that way.
    """
    length = raw.FPDFTextObj_GetText(obj, textpage.raw, None, 0)
    if length <= 2:
        return
    buffer = (ctypes.c_ushort * (length // 2))()
    raw.FPDFTextObj_GetText(obj, textpage.raw, buffer, length)
    content = bytes(buffer)[: length - 2].decode("utf-16-le", errors="replace").strip()
    if not content:
        return

    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    if not raw.FPDFPageObj_GetBounds(
        obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)
    ):
        return
    # Bounds include the object's own matrix; the parent forms' matrices remain to apply.
    corners = [
        to_sheet(x, y, parent)
        for x, y in (
            (left.value, bottom.value),
            (right.value, bottom.value),
            (right.value, top.value),
            (left.value, top.value),
        )
    ]
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    placed = _compose(matrix, parent)
    rotation = math.degrees(math.atan2(placed[1], placed[0]))
    size = ctypes.c_float()
    scale = math.sqrt(abs(placed[0] * placed[3] - placed[1] * placed[2])) or 1.0
    height = (
        size.value * scale * POINTS_TO_MM
        if raw.FPDFTextObj_GetFontSize(obj, ctypes.byref(size)) and size.value > 0
        else max(ys) - min(ys)
    )
    builder.text(
        content,
        (min(xs), min(ys), max(xs), max(ys)),
        builder.group(),
        height=round(height, 3),
        rotation=round(rotation, 2),
    )


def _pdfplumber(payload: bytes, index: int) -> bytes:
    import io

    import pdfplumber

    builder = Builder(Method.PDF_FALLBACK)
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        if not 0 <= index < len(pdf.pages):
            raise PdfUnreadable(f"this PDF has no page {index + 1}")
        page = pdf.pages[index]
        # pdfplumber measures from the top-left in points, which is the sheet frame already.
        for item in list(page.lines) + list(page.curves) + list(page.rects):
            points = item.get("pts") or [(item["x0"], item["top"]), (item["x1"], item["bottom"])]
            flat = [value * POINTS_TO_MM for point in points for value in point]
            stroke = item.get("stroking_color")
            builder.polyline(
                flat,
                builder.group(),
                closed=item.get("object_type") == "rect",
                color=_rgb(stroke),
                lineweight=round(float(item.get("linewidth") or 0) * POINTS_TO_MM, 4),
            )
        for word in page.extract_words(keep_blank_chars=False, use_text_flow=False):
            box = (
                word["x0"] * POINTS_TO_MM,
                word["top"] * POINTS_TO_MM,
                word["x1"] * POINTS_TO_MM,
                word["bottom"] * POINTS_TO_MM,
            )
            builder.text(word["text"], box, builder.group(), height=box[3] - box[1])
    return to_parquet(builder.table())


def _rgb(color: Any) -> int:
    if isinstance(color, (list, tuple)) and len(color) == 3:
        red, green, blue = (max(0, min(255, round(float(part) * 255))) for part in color)
        return (red << 16) | (green << 8) | blue
    return -1

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
        return {"parquet": _pdfium(payload, index), "method": str(Method.PDF_VECTOR)}
    except PdfUnreadable:
        raise
    except Exception as failure:
        return {
            "parquet": _pdfplumber(payload, index),
            "method": str(Method.PDF_FALLBACK),
            "primary_failure": f"{type(failure).__name__}: {failure}"[:300],
        }


def _pdfium(payload: bytes, index: int) -> bytes:
    import pypdfium2.raw as raw

    document = _open(payload)
    try:
        if not 0 <= index < len(document):
            raise PdfUnreadable(f"this PDF has no page {index + 1}")
        page = document[index]
        left, _bottom, _right, top = page.get_cropbox()
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

        walk(page.raw, raw.FPDFPage_CountObjects(page.raw), raw.FPDFPage_GetObject, IDENTITY, 0)
        _text(page, builder, left, top)
        return to_parquet(builder.table())
    finally:
        document.close()


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


def _text(page: Any, builder: Builder, left: float, top: float) -> None:
    """Text as spans with boxes, through PDFium's text page, which resolves forms itself."""
    import pypdfium2.raw as raw

    textpage = page.get_textpage()
    for rect in range(textpage.count_rects()):
        x0, y0, x1, y1 = textpage.get_rect(rect)
        content = textpage.get_text_bounded(x0, y0, x1, y1).strip()
        if not content:
            continue
        char = raw.FPDFText_GetCharIndexAtPos(textpage.raw, (x0 + x1) / 2, (y0 + y1) / 2, 2, 2)
        angle = math.degrees(raw.FPDFText_GetCharAngle(textpage.raw, char)) if char >= 0 else 0.0
        box = (
            (x0 - left) * POINTS_TO_MM,
            (top - y1) * POINTS_TO_MM,
            (x1 - left) * POINTS_TO_MM,
            (top - y0) * POINTS_TO_MM,
        )
        height = (
            (y1 - y0) * POINTS_TO_MM
            if abs(angle) < 45 or abs(angle) > 135
            else ((x1 - x0) * POINTS_TO_MM)
        )
        builder.text(content, box, builder.group(), height=height, rotation=round(angle, 2))


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

"""Reading a drawing list or transmittal workbook. Runs in the sandbox, returns plain data.

A transmittal is a table with a drawing number column and a revision column, and usually a
title and an issue date. Its header row is found by those columns, wherever it sits on the
sheet, because transmittals carry a letterhead above the table.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

NUMBER_HEADER = re.compile(
    r"^(DRAWING|DWG|DRG|SHEET)\s*(NO\.?|NUMBER|NUM\.?|REF\.?)$|^DRAWING$", re.I
)
REVISION_HEADER = re.compile(r"^REV(ISION)?\.?(\s*NO\.?)?$", re.I)
DATE_HEADER = re.compile(r"^(ISSUE\s+)?DATE(\s+ISSUED)?$|^ISSUED$", re.I)
TITLE_HEADER = re.compile(r"^(DRAWING\s+)?(TITLE|DESCRIPTION)$", re.I)
MAX_ROWS = 2_000


def transmittal_rows(payload: bytes) -> list[dict[str, Any]]:
    """Every drawing listed, as {sheet_number, revision, issued_on, title}. [] if none."""
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        found: list[dict[str, Any]] = []
        for sheet in workbook.worksheets:
            columns: dict[str, int] | None = None
            for row in sheet.iter_rows(max_row=MAX_ROWS, values_only=True):
                cells = ["" if value is None else value for value in row]
                if columns is None:
                    columns = _header(cells)
                    continue
                number = _text(cells, columns["number"])
                revision = _text(cells, columns["revision"])
                if not number or not revision:
                    continue
                found.append(
                    {
                        "sheet_number": number.upper(),
                        "revision": revision.upper(),
                        "issued_on": _date(cells, columns.get("date")),
                        "title": _text(cells, columns.get("title")) or None,
                    }
                )
        return found
    finally:
        workbook.close()


def _header(cells: list[Any]) -> dict[str, int] | None:
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        text = " ".join(str(cell).split())
        for name, pattern in (
            ("number", NUMBER_HEADER),
            ("revision", REVISION_HEADER),
            ("date", DATE_HEADER),
            ("title", TITLE_HEADER),
        ):
            if name not in columns and pattern.match(text):
                columns[name] = index
    return columns if "number" in columns and "revision" in columns else None


def _text(cells: list[Any], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return " ".join(str(cells[index]).split())


def _date(cells: list[Any], index: int | None) -> str | None:
    if index is None or index >= len(cells):
        return None
    value = cells[index]
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    from firebid.drawings.title_block import parse_date

    parsed = parse_date(str(value)) if value else None
    return parsed.isoformat() if parsed else None

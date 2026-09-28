"""Reading a client's bill of quantities (FR-BOQ-02).

Runs in the parser sandbox: the workbook is a file from outside the company. It is opened
with openpyxl in read-only mode, which never writes; the original bytes are kept as they
arrived, and only `boq.xlsx_patch` ever produces a priced copy.

Each sheet's header row is found by the words consultants put there ("Item", "Description",
"Unit", "Qty", "Rate", "Amount"). A sheet whose header is found is a bill; its rows below the
header become sections (a letter and a heading), lines (a description with a unit, quantity
or amount), provisional sums and lump sums, and sub-totals, which are read but are not lines.

When no sheet's header is clear, or one field matches two columns, the layout is ambiguous:
the model is asked to propose a column mapping, which a person confirms (`agents`), and the
sheet is read again with it.

Pure apart from openpyxl: bytes in, a description of the bill out.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl.utils import get_column_letter

FIELDS = ("item", "description", "unit", "quantity", "rate", "amount")
REQUIRED = ("description", "quantity")
# What a header cell says, per field. Whole words, case-insensitive.
WORDS: dict[str, tuple[str, ...]] = {
    "item": ("item", "item no", "ref", "s/n", "no"),
    "description": ("description", "particulars", "description of works", "work"),
    "unit": ("unit", "uom", "units"),
    "quantity": ("qty", "quantity", "quant", "qty."),
    "rate": ("rate", "unit rate", "rate (s$)", "rate s$", "unit price", "price"),
    "amount": ("amount", "total", "amount (s$)", "amount s$", "s$", "sum"),
}
PROVISIONAL = re.compile(r"\bprovisional\b|\bp\.?\s?c\.?\s?sums?\b|\bprime cost\b", re.I)
LUMP_UNITS = {"sum", "item", "ls", "lump sum", "lot", "ps", "p.s."}
TOTAL = re.compile(r"\btotal\b|carried to|brought forward|collection", re.I)
SECTION_ITEM = re.compile(r"^[A-Z]{1,2}$")
HEADER_SCAN_ROWS = 40


class Ambiguous(ValueError):
    """The layout could not be read with confidence; the model is asked, a person confirms."""


@dataclass
class ReadLine:
    row: int
    kind: str  # line | provisional | lump_sum
    section: str | None
    item_no: str | None
    description: str
    unit: str | None
    quantity: Decimal | None
    rate: Decimal | None
    amount: Decimal | None
    amount_is_formula: bool
    rate_cell: str | None
    amount_cell: str | None


@dataclass
class ReadSheet:
    name: str
    header_row: int
    columns: dict[str, int]  # field -> column number (1-based)
    lines: list[ReadLine] = field(default_factory=list)
    subtotals: int = 0


@dataclass
class ReadBook:
    sheets: list[ReadSheet]
    skipped: list[str]  # sheet names that are not bills (a summary, a hidden list)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = _text(value).replace(",", "").replace("S$", "").replace("$", "")
    if not text or text.startswith("="):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _field_of(cell: str) -> str | None:
    words = re.sub(r"\s+", " ", cell.lower().strip(" :"))
    for name, options in WORDS.items():
        if words in options:
            return name
    return None


def find_header(rows: list[tuple[Any, ...]]) -> tuple[int, dict[str, int]] | None:
    """The row (1-based) whose cells name the most fields, and which column each is in.

    Raises `Ambiguous` when a field is named twice on the best row.
    """
    best: tuple[int, dict[str, int]] | None = None
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS], start=1):
        found: dict[str, int] = {}
        doubled = False
        for column, value in enumerate(row, start=1):
            name = _field_of(_text(value))
            if name is None:
                continue
            if name in found:
                doubled = True
            found.setdefault(name, column)
        if not all(name in found for name in REQUIRED) or len(found) < 4:
            continue
        if doubled:
            raise Ambiguous(f"row {index} names a field twice")
        if best is None or len(found) > len(best[1]):
            best = (index, found)
    return best


def read_sheet(
    name: str, rows: list[tuple[Any, ...]], columns: dict[str, int], header_row: int
) -> ReadSheet:
    """The bill below its header: sections, lines, provisional sums; sub-totals counted."""
    sheet = ReadSheet(name, header_row, columns)
    section: str | None = None
    in_provisional = False

    def at(row: tuple[Any, ...], name: str) -> Any:
        column = columns.get(name)
        return row[column - 1] if column and column <= len(row) else None

    for index, row in enumerate(rows[header_row:], start=header_row + 1):
        item = _text(at(row, "item"))
        description = _text(at(row, "description"))
        unit = _text(at(row, "unit")) or None
        quantity_value = at(row, "quantity")
        amount_value = at(row, "amount")
        quantity = _number(quantity_value)
        amount = _number(amount_value)
        amount_is_formula = _text(amount_value).startswith("=")
        if not description and not item:
            continue
        if TOTAL.search(description) and unit is None and quantity is None:
            sheet.subtotals += 1
            continue
        if (
            description
            and unit is None
            and quantity is None
            and amount is None
            and not amount_is_formula
            and (SECTION_ITEM.match(item) or not item)
        ):
            section = f"{item} {description}".strip()
            in_provisional = bool(PROVISIONAL.search(description))
            continue
        if not description:
            continue
        lump = (unit or "").lower() in LUMP_UNITS and quantity is None
        kind = (
            "provisional"
            if in_provisional or PROVISIONAL.search(description)
            else ("lump_sum" if lump else "line")
        )
        rate_column = columns.get("rate")
        amount_column = columns.get("amount")
        sheet.lines.append(
            ReadLine(
                row=index,
                kind=kind,
                section=section,
                item_no=item or None,
                description=description,
                unit=unit,
                quantity=quantity,
                rate=_number(at(row, "rate")),
                amount=amount,
                amount_is_formula=amount_is_formula,
                rate_cell=f"{get_column_letter(rate_column)}{index}" if rate_column else None,
                amount_cell=f"{get_column_letter(amount_column)}{index}" if amount_column else None,
            )
        )
    return sheet


def read_workbook(
    payload: bytes, confirmed: dict[str, tuple[int, dict[str, int]]] | None = None
) -> ReadBook:
    """Every bill in the workbook. `confirmed` gives a person's header row and columns for a
    sheet whose layout was ambiguous (sheet name -> (header row, field -> column))."""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False)
    sheets: list[ReadSheet] = []
    skipped: list[str] = []
    ambiguous: list[str] = []
    try:
        for worksheet in book.worksheets:
            if getattr(worksheet, "sheet_state", "visible") != "visible":
                skipped.append(worksheet.title)
                continue
            rows = [tuple(row) for row in worksheet.iter_rows(values_only=True)]
            if confirmed and worksheet.title in confirmed:
                header_row, columns = confirmed[worksheet.title]
            else:
                try:
                    found = find_header(rows)
                except Ambiguous:
                    ambiguous.append(worksheet.title)
                    continue
                if found is None:
                    skipped.append(worksheet.title)
                    continue
                header_row, columns = found
            sheets.append(read_sheet(worksheet.title, rows, columns, header_row))
    finally:
        book.close()
    if ambiguous or not sheets:
        raise Ambiguous(
            "the bill's columns could not be read with confidence"
            + (f" on {', '.join(ambiguous)}" if ambiguous else ": no sheet has a clear header")
        )
    return ReadBook(sheets, skipped)


def header_preview(payload: bytes, rows: int = 15) -> dict[str, list[list[str]]]:
    """The top of every visible sheet, as text: what the model is shown to propose columns."""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False)
    try:
        return {
            worksheet.title: [
                [_text(v)[:60] for v in row]
                for row in worksheet.iter_rows(max_row=rows, values_only=True)
            ]
            for worksheet in book.worksheets
            if getattr(worksheet, "sheet_state", "visible") == "visible"
        }
    finally:
        book.close()


def read_json(payload: bytes, confirmed: dict[str, Any] | None = None) -> dict[str, Any]:
    """The sandbox's entry point: the bills as plain data, or the top rows when ambiguous.

    `confirmed` is JSON-shaped: sheet name -> [header row, {field: column}].
    """
    from dataclasses import asdict

    columns = (
        {
            name: (int(v[0]), {k: int(c) for k, c in dict(v[1]).items()})
            for name, v in confirmed.items()
        }
        if confirmed
        else None
    )
    try:
        book = read_workbook(payload, columns)
    except Ambiguous as unclear:
        return {"ambiguous": str(unclear), "preview": header_preview(payload)}
    return {"sheets": [asdict(sheet) for sheet in book.sheets], "skipped": book.skipped}

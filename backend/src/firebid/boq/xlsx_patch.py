"""Write numbers into a client's workbook without touching anything else (FR-BOQ-04, NFR-13).

A client BOQ is the client's file: its styles, formulas, merged cells, hidden sheets, print
settings, images, charts, comments, data validation, defined names and external links must
come back exactly as they went out. openpyxl drops some of those when it saves, so it never
writes a client workbook. This module edits the package directly:

* the package is unzipped part by part;
* in each affected worksheet part, only the target `<c>` elements are replaced, by splicing
  the original bytes, so every other byte of the part is the client's;
* a number is written as a numeric cell (`<v>`), never a shared or inline string, keeping
  the cell's style;
* a cell holding a formula is refused: amounts that are formulas are left for Excel to
  recompute, and the workbook is flagged to recalculate everything when it is opened;
* the package is written back with the original part order, compression and timestamps.

Pure: bytes and edits in, bytes out.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal

from defusedxml import ElementTree

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")


class PatchRefused(ValueError):
    """An edit that would change something it must not, such as a formula."""


@dataclass(frozen=True)
class CellEdit:
    sheet: str  # the sheet's name as the client sees it
    cell: str  # "E12"
    value: Decimal


def column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - ord("A") + 1)
    return number


def split_ref(ref: str) -> tuple[str, int]:
    match = CELL_REF.match(ref)
    if match is None:
        raise PatchRefused(f"{ref!r} is not a cell reference")
    return match.group(1), int(match.group(2))


def sheet_parts(package: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name to the path of its worksheet part, from the workbook and its relations."""
    workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
    relations = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.get("Id"): rel.get("Target", "") for rel in relations.iter(f"{{{RELS}}}Relationship")
    }
    parts = {}
    for sheet in workbook.iter(f"{{{MAIN}}}sheet"):
        target = targets.get(sheet.get(f"{{{DOC_RELS}}}id"), "")
        path = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
        parts[sheet.get("name", "")] = posixpath.normpath(path)
    return parts


def _number(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


# A cell element: self-closing, or with content up to its closing tag. Cells never nest.
_CELL = r'<c\b[^>]*?\br="{ref}"[^>]*?(?:/>|>.*?</c>)'
_ROW = r'<row\b[^>]*?\br="{row}"[^>]*?(?:/>|>.*?</row>)'


def _patched_cell(original: str | None, ref: str, value: Decimal) -> str:
    style = ""
    if original is not None:
        opening, _, content = original.partition(">")
        if not opening.endswith("/") and "<f" in content:
            raise PatchRefused(f"{ref} holds a formula; it is left for Excel to recompute")
        found = re.search(r'\bs="(\d+)"', opening)
        if found:
            style = f' s="{found.group(1)}"'
    return f'<c r="{ref}"{style}><v>{_number(value)}</v></c>'


def patch_sheet(xml: bytes, edits: dict[str, Decimal]) -> bytes:
    """Replace or insert the target cells in one worksheet part, leaving the rest as bytes."""
    text = xml.decode("utf-8")
    for ref, value in sorted(edits.items(), key=lambda e: (split_ref(e[0])[1], e[0])):
        letters, row_number = split_ref(ref)
        cell = re.search(_CELL.format(ref=ref), text, flags=re.S)
        if cell is not None:
            replacement = _patched_cell(cell.group(0), ref, value)
            text = text[: cell.start()] + replacement + text[cell.end() :]
            continue
        new_cell = _patched_cell(None, ref, value)
        row = re.search(_ROW.format(row=row_number), text, flags=re.S)
        if row is None:
            text = _insert_row(text, row_number, new_cell)
            continue
        text = (
            text[: row.start()] + _insert_cell(row.group(0), letters, new_cell) + text[row.end() :]
        )
    return text.encode("utf-8")


def _insert_cell(row: str, letters: str, cell: str) -> str:
    """Put a cell into its row in column order."""
    if row.endswith("/>"):
        return row[:-2] + ">" + cell + "</row>"
    target = column_number(letters)
    for match in re.finditer(r'<c\b[^>]*?\br="([A-Z]+)\d+"', row):
        if column_number(match.group(1)) > target:
            return row[: match.start()] + cell + row[match.start() :]
    closing = row.rindex("</row>")
    return row[:closing] + cell + row[closing:]


def _insert_row(text: str, row_number: int, cell: str) -> str:
    """Put a new row into sheetData in row order."""
    new_row = f'<row r="{row_number}">{cell}</row>'
    for match in re.finditer(r'<row\b[^>]*?\br="(\d+)"', text):
        if int(match.group(1)) > row_number:
            return text[: match.start()] + new_row + text[match.start() :]
    if "<sheetData/>" in text:
        return text.replace("<sheetData/>", f"<sheetData>{new_row}</sheetData>", 1)
    closing = text.index("</sheetData>")
    return text[:closing] + new_row + text[closing:]


def recalculate_on_load(workbook_xml: bytes) -> bytes:
    """Ask Excel to recompute every formula when the file opens."""
    text = workbook_xml.decode("utf-8")
    calc = re.search(r"<calcPr\b[^>]*?/?>", text)
    if calc is not None:
        element = calc.group(0)
        if "fullCalcOnLoad=" in element:
            patched = re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', element)
        else:
            patched = element.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1)
        text = text[: calc.start()] + patched + text[calc.end() :]
    else:
        closing = text.index("</workbook>")
        # calcPr belongs after the defined names and before any later extension elements.
        anchor = max(text.rfind("</definedNames>"), text.rfind("</sheets>"))
        tag = "</definedNames>" if text.rfind("</definedNames>") == anchor else "</sheets>"
        at = anchor + len(tag) if anchor >= 0 else closing
        text = text[:at] + '<calcPr fullCalcOnLoad="1"/>' + text[at:]
    return text.encode("utf-8")


def patch_workbook(original: bytes, edits: list[CellEdit]) -> bytes:
    """The client's workbook with only `edits` changed, and recalculation asked for."""
    source = zipfile.ZipFile(io.BytesIO(original))
    parts = sheet_parts(source)
    by_part: dict[str, dict[str, Decimal]] = {}
    for edit in edits:
        part = parts.get(edit.sheet)
        if part is None:
            raise PatchRefused(f"the workbook has no sheet {edit.sheet!r}")
        by_part.setdefault(part, {})[edit.cell] = edit.value
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename in by_part:
                data = patch_sheet(data, by_part[info.filename])
            elif info.filename == "xl/workbook.xml" and edits:
                data = recalculate_on_load(data)
            copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            copy.comment = info.comment
            copy.extra = info.extra
            target.writestr(copy, data)
    return out.getvalue()

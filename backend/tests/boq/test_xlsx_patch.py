"""The priced client workbook: only the target cells change (FR-BOQ-04, NFR-13).

The client's synthetic bill is priced by patching its package. Every part other than the
priced worksheet and the workbook's recalculation flag comes back byte for byte; inside the
worksheet, only the rate cells (and plain-value amounts) differ. The image, chart, comments,
data validation, defined names, merged cells, hidden sheet and print settings all survive.
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from firebid.boq.xlsx_patch import CellEdit, PatchRefused, patch_workbook, sheet_parts
from firebid.evals.synthetic_boq import BILL, client_boq

pytestmark = [pytest.mark.req("FR-BOQ-04"), pytest.mark.req("NFR-13")]

RATES = {"A1": "45.50", "A2": "45.50", "B1": "182.00", "B3": "64.20", "C1": "1250.00"}


def priced() -> tuple[bytes, bytes, dict[str, str]]:
    fixture = client_boq()
    edits = [CellEdit(BILL, fixture.cells[item][1], Decimal(rate)) for item, rate in RATES.items()]
    # The riser's amount is typed in, not a formula: it is priced as quantity x rate.
    edits.append(CellEdit(BILL, fixture.cells["B5"][1], Decimal("190.00")))
    edits.append(CellEdit(BILL, fixture.cells["B5"][2], Decimal("760.00")))
    targets = {e.cell: str(e.value) for e in edits}
    return fixture.payload, patch_workbook(fixture.payload, edits), targets


def parts(payload: bytes) -> list[zipfile.ZipInfo]:
    return zipfile.ZipFile(io.BytesIO(payload)).infolist()


def read(payload: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(payload)).read(name)


def cells(xml: bytes) -> dict[str, str]:
    text = xml.decode("utf-8")
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r'<c\b[^>]*?\br="([A-Z]+\d+)"[^>]*?(?:/>|>.*?</c>)', text, re.S)
    }


def test_the_package_keeps_its_parts_order_and_compression() -> None:
    original, patched, _ = priced()

    before, after = parts(original), parts(patched)

    assert [p.filename for p in after] == [p.filename for p in before]
    assert [p.compress_type for p in after] == [p.compress_type for p in before]
    assert [p.date_time for p in after] == [p.date_time for p in before]


def test_every_other_part_is_byte_identical() -> None:
    original, patched, _ = priced()
    sheet = sheet_parts(zipfile.ZipFile(io.BytesIO(original)))[BILL]

    changed = [
        p.filename
        for p in parts(original)
        if read(original, p.filename) != read(patched, p.filename)
    ]

    # The workbook part changes only if it lacked the recalculation flag (this one has it).
    assert sheet in changed and set(changed) <= {sheet, "xl/workbook.xml"}
    # Everything a naive re-save loses is among the untouched parts.
    names = {p.filename for p in parts(original)}
    assert {"xl/media/image1.png", "xl/charts/chart1.xml", "xl/comments/comment1.xml"} <= names


def test_only_the_target_cells_differ_in_the_patched_sheet() -> None:
    original, patched, targets = priced()
    sheet = sheet_parts(zipfile.ZipFile(io.BytesIO(original)))[BILL]
    before, after = cells(read(original, sheet)), cells(read(patched, sheet))

    differing = {ref for ref in before.keys() | after.keys() if before.get(ref) != after.get(ref)}

    assert differing == set(targets)
    for ref, value in targets.items():
        assert re.search(rf"<v>{re.escape(value.rstrip('0').rstrip('.'))}</v>", after[ref])
        assert 't="s"' not in after[ref] and "<f" not in after[ref]
        # The cell keeps the client's style.
        style = re.search(r'\bs="(\d+)"', before[ref])
        assert style is None or f's="{style.group(1)}"' in after[ref]
    # Outside the cells, the part is the client's text, byte for byte.
    strip = re.compile(r'<c\b[^>]*?\br="[A-Z]+\d+"[^>]*?(?:/>|>.*?</c>)', re.S)
    assert strip.sub("", read(patched, sheet).decode()) == strip.sub(
        "", read(original, sheet).decode()
    )


def test_the_workbook_only_gains_the_recalculation_flag() -> None:
    original, patched, _ = priced()

    before = read(original, "xl/workbook.xml").decode()
    after = read(patched, "xl/workbook.xml").decode()

    assert 'fullCalcOnLoad="1"' in after
    assert re.sub(r'<calcPr\b[^>]*/>|\s*fullCalcOnLoad="1"', "", after) == re.sub(
        r"<calcPr\b[^>]*/>", "", before
    )


def test_the_priced_workbook_reads_back_with_everything_in_place() -> None:
    _, patched, _ = priced()
    fixture = client_boq()

    book = load_workbook(io.BytesIO(patched))
    bill = book[BILL]

    assert bill[fixture.cells["B1"][1]].value == 182
    assert (
        bill[fixture.cells["A1"][2]].value
        == f"=D{fixture.cells['A1'][0]}*E{fixture.cells['A1'][0]}"
    )
    assert bill[fixture.cells["B5"][2]].value == 760
    assert "BillOneTotal" in book.defined_names
    assert book["Lists"].sheet_state == "hidden"
    assert "A1:F1" in {str(r) for r in bill.merged_cells.ranges}
    assert bill.data_validations.dataValidation
    assert bill["E10"].comment is not None


def test_a_formula_cell_is_never_overwritten() -> None:
    fixture = client_boq()

    with pytest.raises(PatchRefused, match="formula"):
        patch_workbook(fixture.payload, [CellEdit(BILL, fixture.cells["A1"][2], Decimal("100"))])


def test_a_cell_the_sheet_does_not_have_yet_is_added_in_order() -> None:
    fixture = client_boq()

    patched = patch_workbook(fixture.payload, [CellEdit(BILL, "G10", Decimal("1.5"))])

    book = load_workbook(io.BytesIO(patched))
    assert book[BILL]["G10"].value == 1.5
    assert book[BILL]["F10"].value == "=D10*E10"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # As Excel saves it: a calcPr with no flag.
        (
            '<workbook><sheets/><calcPr calcId="191029"/></workbook>',
            '<workbook><sheets/><calcPr fullCalcOnLoad="1" calcId="191029"/></workbook>',
        ),
        # No calcPr at all: one is added after the defined names.
        (
            "<workbook><sheets/><definedNames><definedName/></definedNames></workbook>",
            "<workbook><sheets/><definedNames><definedName/></definedNames>"
            '<calcPr fullCalcOnLoad="1"/></workbook>',
        ),
        # A flag set to 0 is turned on.
        (
            '<workbook><calcPr fullCalcOnLoad="0"/></workbook>',
            '<workbook><calcPr fullCalcOnLoad="1"/></workbook>',
        ),
    ],
)
def test_the_workbook_is_asked_to_recalculate_on_opening(given: str, expected: str) -> None:
    from firebid.boq.xlsx_patch import recalculate_on_load

    assert recalculate_on_load(given.encode()).decode() == expected


def test_a_text_cell_as_excel_writes_it_becomes_a_number_in_its_own_style() -> None:
    from firebid.boq.xlsx_patch import patch_sheet

    xml = (
        b'<worksheet><sheetData><row r="10" spans="1:6">'
        b'<c r="A10" t="s"><v>3</v></c><c r="E10" s="7" t="s"><v>12</v></c>'
        b'<c r="F10" s="8"><f>D10*E10</f><v>0</v></c></row>'
        b'<row r="12"><c r="B12" t="s"><v>4</v></c></row></sheetData></worksheet>'
    )

    patched = patch_sheet(xml, {"E10": Decimal("45.50"), "C12": Decimal("2"), "E11": Decimal("1")})

    assert patched.decode() == (
        '<worksheet><sheetData><row r="10" spans="1:6">'
        '<c r="A10" t="s"><v>3</v></c><c r="E10" s="7"><v>45.5</v></c>'
        '<c r="F10" s="8"><f>D10*E10</f><v>0</v></c></row>'
        '<row r="11"><c r="E11"><v>1</v></c></row>'
        '<row r="12"><c r="B12" t="s"><v>4</v></c><c r="C12"><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )

"""Reading a client's bill (FR-BOQ-02): header, sections, lines, provisional sums."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from openpyxl import Workbook

from firebid.boq.reader import Ambiguous, read_workbook
from firebid.evals.synthetic_boq import BILL, SUMMARY, client_boq

pytestmark = pytest.mark.req("FR-BOQ-02")


def test_the_synthetic_bill_is_read_line_by_line() -> None:
    fixture = client_boq()

    book = read_workbook(fixture.payload)

    [bill] = book.sheets
    assert bill.name == BILL and bill.header_row == 7
    assert bill.columns == {
        "item": 1,
        "description": 2,
        "unit": 3,
        "quantity": 4,
        "rate": 5,
        "amount": 6,
    }
    assert set(book.skipped) == {SUMMARY, "Lists"}
    by_item = {line.item_no: line for line in bill.lines}
    assert set(by_item) == set(fixture.cells)
    pendent = by_item["A1"]
    assert (pendent.unit, pendent.quantity, pendent.section) == (
        "nr",
        Decimal(16),
        "A SPRINKLER HEADS",
    )
    assert pendent.amount_is_formula and pendent.rate_cell == "E10" and pendent.amount_cell == "F10"
    assert by_item["B5"].amount_is_formula is False
    assert {by_item["D1"].kind, by_item["D2"].kind} == {"provisional"}
    assert by_item["A1"].kind == "line"
    # Four section sub-totals and the bill total are read, not taken as lines.
    assert bill.subtotals == 5


def test_a_header_worded_differently_is_still_found() -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Some Project"])
    sheet.append([])
    sheet.append(["Ref", "Particulars", "UOM", "Quantity", "Unit Rate", "Amount (S$)"])
    sheet.append(["1", "Pendent sprinkler", "nr", 10, None, "=D4*E4"])
    out = io.BytesIO()
    book.save(out)

    [bill] = read_workbook(out.getvalue()).sheets

    assert bill.header_row == 3 and bill.columns["rate"] == 5
    assert bill.lines[0].quantity == Decimal(10)


def test_an_ambiguous_layout_is_not_guessed() -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Item", "Description", "Qty", "Qty", "Rate", "Amount"])
    sheet.append(["1", "Pendent sprinkler", 10, 12, None, None])
    out = io.BytesIO()
    book.save(out)

    with pytest.raises(Ambiguous, match="could not be read"):
        read_workbook(out.getvalue())

    # A person's confirmed columns read it.
    confirmed = {
        sheet.title: (1, {"item": 1, "description": 2, "quantity": 4, "rate": 5, "amount": 6})
    }
    [bill] = read_workbook(out.getvalue(), confirmed).sheets
    assert bill.lines[0].quantity == Decimal(12)

"""A company rate list for the synthetic installation (P1-10). Rates are invented.

Set against `synthetic_qto`'s takeoff, it prices most lines exactly, and leaves the known
gaps: a tee whose only entry names a brand the line does not (a partial match, for the model
to propose and a person to confirm), a check valve and another tee with no entry at all
(unpriced). One pipe rate has expired; one ends before a tender validity running past
October 2026.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from openpyxl import Workbook

HEADER = (
    "Type",
    "DN",
    "Material",
    "Schedule",
    "Joining",
    "Brand",
    "Description",
    "Unit",
    "Rate (SGD)",
    "Source type",
    "Source reference",
    "Effective from",
    "Valid until",
)


@dataclass(frozen=True)
class RateRow:
    type: str
    dn: str
    description: str
    unit: str
    rate: str
    source_type: str
    source_reference: str
    effective_from: date
    valid_until: date | None
    brand: str = ""
    material: str = ""
    schedule: str = ""
    joining: str = ""


SHORT = date(2026, 10, 31)  # before a tender validity that runs past October 2026
EXPIRED = date(2026, 6, 30)

RATES: tuple[RateRow, ...] = (
    RateRow(
        "sprinkler_pendent",
        "",
        "Pendent sprinkler head, K80, 68C",
        "nr",
        "38.50",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    RateRow(
        "sprinkler_upright",
        "",
        "Upright sprinkler head, K80, 68C",
        "nr",
        "38.50",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    RateRow(
        "sprinkler_sidewall",
        "",
        "Sidewall sprinkler head, K80, 68C",
        "nr",
        "52.00",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    RateRow(
        "pipe",
        "150",
        "Black steel pipe DN150",
        "m",
        "68.20",
        "quotation",
        "Q-2026-1001",
        date(2026, 8, 1),
        SHORT,
    ),
    RateRow(
        "pipe",
        "100",
        "Black steel pipe DN100",
        "m",
        "45.60",
        "PO",
        "PO-889",
        date(2026, 1, 15),
        EXPIRED,
    ),
    RateRow(
        "pipe",
        "50",
        "Black steel pipe DN50",
        "m",
        "21.35",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    RateRow(
        "pipe",
        "25",
        "Black steel pipe DN25",
        "m",
        "12.10",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    RateRow(
        "gate_valve",
        "150",
        "Gate valve DN150, flanged",
        "nr",
        "890.00",
        "quotation",
        "Q-2026-1002",
        date(2026, 8, 1),
        date(2027, 6, 30),
    ),
    RateRow(
        "fitting_reducer",
        "150",
        "Concentric reducer DN150",
        "nr",
        "64.00",
        "company standard",
        "CS-2026",
        date(2026, 1, 1),
        date(2027, 12, 31),
    ),
    # Names a brand the takeoff line does not: a partial match only.
    RateRow(
        "fitting_tee",
        "100x50",
        "Grooved tee 100x50",
        "nr",
        "41.80",
        "quotation",
        "Q-2026-1003",
        date(2026, 8, 1),
        date(2027, 6, 30),
        brand="Victaulic",
    ),
)


def rate_list(
    rows: tuple[RateRow, ...] = RATES, *, extra: list[tuple[object, ...]] | None = None
) -> bytes:
    """The rate list as a workbook, with a title above the header as people make them."""
    book = Workbook()
    sheet = book.active
    if sheet is None:  # pragma: no cover - a new workbook always has one
        sheet = book.create_sheet()
    sheet.title = "Rates"
    sheet.append(["COMPANY RATE LIBRARY (SYNTHETIC)"])
    sheet.append([])
    sheet.append(list(HEADER))
    for row in rows:
        sheet.append(
            [
                row.type,
                row.dn,
                row.material,
                row.schedule,
                row.joining,
                row.brand,
                row.description,
                row.unit,
                float(row.rate),
                row.source_type,
                row.source_reference,
                row.effective_from,
                row.valid_until,
            ]
        )
    for values in extra or []:
        sheet.append(list(values))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()

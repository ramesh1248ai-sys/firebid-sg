"""The company ERP as the platform sees it: items, purchase orders, historical costs (FR-CST-08).

Which ERP the company uses is decision D4. Until it is named, the platform reads the ERP's
exports from a file, behind the same interface an API adapter will fill:

* `ErpAdapter`: what any ERP connection provides: the item master, purchase order lines and
  historical project costs, as plain records.
* `FileErp`: the file-based adapter. A workbook with sheets `Items`, `PurchaseOrders` and
  `HistoricalCosts`, each with a header row naming its columns. Every problem is reported
  by sheet, row and column, and a file with any problem imports nothing.

Purchase orders and historical costs become the price history that current prices are
compared with (`pricing.history`). They are never applied to a bill as prices.

`read_json` is the sandbox entry: a file from outside the platform is opened only there.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

SHEETS: dict[str, tuple[tuple[str, bool], ...]] = {
    # sheet -> (column, required)
    "Items": (("code", True), ("description", True), ("unit", True), ("item_key", False)),
    "PurchaseOrders": (
        ("po_number", True),
        ("date", True),
        ("item_key", True),
        ("description", False),
        ("unit", True),
        ("unit_price_sgd", True),
        ("supplier", False),
    ),
    "HistoricalCosts": (
        ("project", True),
        ("date", True),
        ("item_key", True),
        ("unit", True),
        ("unit_price_sgd", True),
    ),
}


@dataclass(frozen=True)
class Item:
    code: str
    description: str
    unit: str
    item_key: str | None


@dataclass(frozen=True)
class PastPrice:
    kind: str  # purchase_order | project
    reference: str  # the PO number, or the project
    on: date
    item_key: str
    unit: str
    unit_price: Decimal
    description: str | None = None
    supplier: str | None = None


class ErpAdapter(Protocol):
    """What the platform needs of an ERP. The file adapter fills it now; an API adapter
    will when decision D4 names the system."""

    def item_master(self) -> list[Item]: ...

    def purchase_orders(self) -> list[PastPrice]: ...

    def historical_costs(self) -> list[PastPrice]: ...


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return price if price > 0 else None


def read_json(payload: bytes) -> dict[str, Any]:
    """An ERP export workbook as plain rows and problems. Sandbox entry."""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    found: dict[str, Any] = {"rows": {}, "problems": []}
    for sheet_name, columns in SHEETS.items():
        if sheet_name not in book.sheetnames:
            found["rows"][sheet_name] = []
            continue
        rows = list(book[sheet_name].iter_rows(values_only=True))
        header = [str(cell or "").strip().lower() for cell in (rows[0] if rows else ())]
        missing = [name for name, required in columns if required and name not in header]
        if missing:
            found["problems"].append(
                {
                    "sheet": sheet_name,
                    "row": 1,
                    "column": ", ".join(missing),
                    "problem": "missing column",
                }
            )
            found["rows"][sheet_name] = []
            continue
        kept = []
        for number, row in enumerate(rows[1:], start=2):
            if not any(cell not in (None, "") for cell in row):
                continue
            record: dict[str, Any] = {}
            for name, required in columns:
                value = (
                    row[header.index(name)]
                    if name in header and header.index(name) < len(row)
                    else None
                )
                text = "" if value is None else str(value).strip()
                if name == "date":
                    day = _day(value)
                    if day is None:
                        found["problems"].append(
                            {
                                "sheet": sheet_name,
                                "row": number,
                                "column": name,
                                "problem": "not a date",
                            }
                        )
                    record[name] = day.isoformat() if day else None
                elif name == "unit_price_sgd":
                    price = _price(value)
                    if price is None:
                        found["problems"].append(
                            {
                                "sheet": sheet_name,
                                "row": number,
                                "column": name,
                                "problem": "not a price above zero",
                            }
                        )
                    record[name] = str(price) if price is not None else None
                else:
                    if required and not text:
                        found["problems"].append(
                            {"sheet": sheet_name, "row": number, "column": name, "problem": "blank"}
                        )
                    record[name] = text or None
            record["row"] = number
            kept.append(record)
        found["rows"][sheet_name] = kept
    return found


class FileErp:
    """The file-based adapter: an ERP export already read into rows (`read_json`)."""

    def __init__(self, found: dict[str, Any]) -> None:
        self._rows: dict[str, list[dict[str, Any]]] = found.get("rows", {})
        self.problems: list[dict[str, Any]] = list(found.get("problems", []))

    def item_master(self) -> list[Item]:
        return [
            Item(row["code"], row["description"], row["unit"].lower(), row.get("item_key"))
            for row in self._rows.get("Items", [])
        ]

    def _past(self, sheet: str, kind: str, reference: str) -> list[PastPrice]:
        return [
            PastPrice(
                kind=kind,
                reference=row[reference],
                on=date.fromisoformat(row["date"]),
                item_key=row["item_key"],
                unit=row["unit"].lower(),
                unit_price=Decimal(row["unit_price_sgd"]),
                description=row.get("description"),
                supplier=row.get("supplier"),
            )
            for row in self._rows.get(sheet, [])
        ]

    def purchase_orders(self) -> list[PastPrice]:
        return self._past("PurchaseOrders", "purchase_order", "po_number")

    def historical_costs(self) -> list[PastPrice]:
        return self._past("HistoricalCosts", "project", "project")

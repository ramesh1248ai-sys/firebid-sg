"""Matching BOQ lines to rate library entries, checking validity, and adding up.

Pure: entries and lines in, answers out. Money is `Money` (Decimal, SGD, 2 dp, half-up).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from firebid.domain.values import Money
from firebid.pricing.keys import ItemKey, unit_of

CONFIG = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"
SOURCES = ("company_standard", "purchase_order", "quotation")


@cache
def settings() -> dict[str, Any]:
    return dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {})


def source_order() -> tuple[str, ...]:
    """Which source wins when two current entries price the same item from the same date."""
    return tuple(settings().get("source_preference") or SOURCES)


@dataclass(frozen=True)
class Entry:
    """A current rate library entry, as matching and checking need it."""

    id: str
    key: ItemKey
    unit: str
    rate: Money
    source_type: str
    source_reference: str
    effective_from: date
    valid_until: date | None
    description: str = ""


# --- Matching -------------------------------------------------------------------------------


def exact(key: ItemKey, unit: str | None, entries: Iterable[Entry]) -> Entry | None:
    """The entry for exactly this item in this unit. Of several, the newest effective one;
    on the same date, the preferred source."""
    wanted = unit_of(unit)
    found = [entry for entry in entries if entry.key == key and unit_of(entry.unit) == wanted]
    if not found or wanted is None:
        return None
    order = source_order()

    def rank(entry: Entry) -> tuple[date, int]:
        preference = order.index(entry.source_type) if entry.source_type in order else len(order)
        return (entry.effective_from, -preference)

    return max(found, key=rank)


def candidates(key: ItemKey, unit: str | None, entries: Iterable[Entry]) -> list[Entry]:
    """Entries for the same kind and size of item in the same unit that differ, or are
    silent, on something else: what the model may propose, and a person decides."""
    wanted = unit_of(unit)
    if wanted is None:
        return []
    return [entry for entry in entries if unit_of(entry.unit) == wanted and key.partial(entry.key)]


# --- Validity -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Warning:
    code: str  # expired | ends_before_tender_validity | tender_validity_unknown
    message: str


def tender_validity_end(
    submission_deadline: datetime | date, validity_days: int | None
) -> date | None:
    """The last day the tender price must hold: submission plus the validity period."""
    if validity_days is None:
        return None
    day = (
        submission_deadline.date()
        if isinstance(submission_deadline, datetime)
        else submission_deadline
    )
    return day + timedelta(days=validity_days)


def warnings(entry: Entry, today: date, tender_end: date | None) -> list[Warning]:
    out = []
    if entry.valid_until is not None and entry.valid_until < today:
        out.append(Warning("expired", f"the rate expired on {entry.valid_until:%d %b %Y}"))
    if tender_end is None:
        out.append(
            Warning(
                "tender_validity_unknown",
                "the tender validity period is not set: the rate cannot be checked against it",
            )
        )
    elif entry.valid_until is not None and entry.valid_until < tender_end:
        out.append(
            Warning(
                "ends_before_tender_validity",
                f"the rate is valid until {entry.valid_until:%d %b %Y}, before the tender "
                f"validity ends on {tender_end:%d %b %Y}",
            )
        )
    return out


# --- Totals ---------------------------------------------------------------------------------


def line_amount(quantity: Decimal, rate: Money) -> Money:
    """Quantity times rate, rounded half-up to the cent."""
    return rate.times(quantity)


@dataclass(frozen=True)
class PricedLine:
    section: str | None
    quantity: Decimal
    rate: Money | None  # None: unpriced
    allowance: Money | None = None  # a provisional or lump sum: the estimator's own figure


@dataclass
class Totals:
    sections: dict[str, Money] = field(default_factory=dict)
    priced: Money = field(default_factory=Money.zero)
    allowances: Money = field(default_factory=Money.zero)
    grand: Money = field(default_factory=Money.zero)
    unpriced: int = 0
    gst_included: bool = False  # never, here: GST is added in P2-04


def totals(lines: Sequence[PricedLine]) -> Totals:
    """Section and grand totals, excluding GST. Unpriced lines are counted, not guessed."""
    out = Totals()
    for line in lines:
        section = line.section or ""
        if line.rate is not None:
            amount = line_amount(line.quantity, line.rate)
            out.priced = out.priced + amount
        elif line.allowance is not None:
            amount = line.allowance
            out.allowances = out.allowances + amount
        else:
            out.unpriced += 1
            continue
        out.sections[section] = out.sections.get(section, Money.zero()) + amount
    out.grand = out.priced + out.allowances
    return out

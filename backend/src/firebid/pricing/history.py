"""A price against what the company has paid before (FR-CST-07).

Each priced item is compared with the historical purchase order and project rates held for
the same item key and unit. The reference is the median of that history: one unusual order
does not move it. A price further from the median than the configured tolerance, either
way, is flagged, with the comparison shown: how many past prices, their range, the median,
the most recent with its reference, and how far this one is from them.

A flag is a prompt to look, never a correction: the platform does not change or suggest a
price (FR-CST-09).

Pure: a price and its history in; a comparison out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

from firebid.pricing import landed

TENTH = Decimal("0.1")


@dataclass(frozen=True)
class Past:
    """One historical price for an item: a purchase order line or a past project's rate."""

    item_key: str
    unit: str
    unit_price: Decimal  # SGD
    on: date
    kind: str  # purchase_order | project
    reference: str


@dataclass(frozen=True)
class Comparison:
    item_key: str
    unit: str
    price: Decimal
    count: int
    low: Decimal | None = None
    high: Decimal | None = None
    median: Decimal | None = None
    latest: Past | None = None
    deviation_percent: Decimal | None = None
    tolerance_percent: Decimal = Decimal(0)

    @property
    def outlier(self) -> bool:
        return (
            self.deviation_percent is not None
            and abs(self.deviation_percent) > self.tolerance_percent
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "unit": self.unit,
            "price": str(self.price),
            "history": self.count,
            "low": str(self.low) if self.low is not None else None,
            "high": str(self.high) if self.high is not None else None,
            "median": str(self.median) if self.median is not None else None,
            "latest": {
                "unit_price": str(self.latest.unit_price),
                "on": self.latest.on.isoformat(),
                "kind": self.latest.kind,
                "reference": self.latest.reference,
            }
            if self.latest
            else None,
            "deviation_percent": str(self.deviation_percent)
            if self.deviation_percent is not None
            else None,
            "tolerance_percent": str(self.tolerance_percent),
            "outlier": self.outlier,
        }


def tolerance(config: dict[str, Any] | None = None) -> Decimal:
    history = (config if config is not None else landed.settings()).get("history") or {}
    return Decimal(str(history.get("outlier_tolerance_percent", 15)))


def compare(
    item_key: str,
    unit: str,
    price: Decimal,
    history: list[Past],
    tolerance_percent: Decimal | None = None,
) -> Comparison:
    """The price beside the history for its item key and unit. With no history there is
    nothing to compare it with, and nothing is flagged."""
    limit = tolerance() if tolerance_percent is None else tolerance_percent
    past = [p for p in history if p.item_key == item_key and p.unit == unit and p.unit_price > 0]
    if not past:
        return Comparison(item_key, unit, price, 0, tolerance_percent=limit)
    prices = sorted(p.unit_price for p in past)
    middle = Decimal(str(median(prices)))
    deviation = ((price - middle) / middle * 100).quantize(TENTH, ROUND_HALF_UP)
    return Comparison(
        item_key=item_key,
        unit=unit,
        price=price,
        count=len(past),
        low=prices[0],
        high=prices[-1],
        median=middle,
        latest=max(past, key=lambda p: p.on),
        deviation_percent=deviation,
        tolerance_percent=limit,
    )

"""Landed cost in SGD, and GST (FR-CST-04, FR-CST-05).

A supplier's price becomes a cost in SGD by steps, each kept as a line:

    price in its currency
    x the recorded FX rate (with its source and date)
    + the FX buffer, a percentage of the converted price
    + freight, insurance and import charges, each a percentage of the buffered price

Which import lines apply depends on the delivery terms: a CIF price already carries freight
and insurance, a delivered price carries everything. The percentages and the terms they
apply to are configuration (`config/pricing.yaml`), to be confirmed by the commercial team.

Prices are held exclusive of GST. GST is worked out on a total, at the rate in force on the
day the bid is priced: the rate table is configuration with effective dates, so a new rate
changes the bids priced from its date and no earlier one.

Arithmetic is Decimal. Each line is kept to four places; a unit cost and every amount of
money is rounded half-up to the cent once, at the end (guardrail 3).

Pure: a price, an FX rate and the configuration in; a breakdown out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from firebid.domain.values import Money

CONFIG = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"
FOUR = Decimal("0.0001")
CENT = Decimal("0.01")
HOME = "SGD"


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class NoFxRate(ValueError):
    """A foreign-currency price with no recorded rate to convert it at."""


@dataclass(frozen=True)
class FxRate:
    """One recorded rate: how many SGD one unit of the currency is, where from, and when."""

    currency: str
    rate: Decimal
    source: str
    as_of: date


@dataclass(frozen=True)
class ImportLine:
    key: str
    label: str
    percent: Decimal
    applies_to: tuple[str, ...]  # delivery terms the line is added for


def import_lines(config: dict[str, Any] | None = None) -> list[ImportLine]:
    found = (config if config is not None else settings()).get("import_costs") or []
    return [
        ImportLine(
            key=str(item["key"]),
            label=str(item["label"]),
            percent=Decimal(str(item["percent"])),
            applies_to=tuple(str(term).upper() for term in item.get("applies_to") or ()),
        )
        for item in found
    ]


def buffer_percent(config: dict[str, Any] | None = None) -> Decimal:
    fx = (config if config is not None else settings()).get("fx") or {}
    return Decimal(str(fx.get("buffer_percent", 0)))


@dataclass(frozen=True)
class CostLine:
    key: str
    label: str
    amount: Decimal  # SGD, four places
    basis: str  # how it was reached, in words

    def as_json(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "amount": str(self.amount),
            "basis": self.basis,
        }


@dataclass(frozen=True)
class Landed:
    """A unit price brought to SGD: every step, and the unit cost they add up to."""

    currency: str
    price: Decimal
    lines: tuple[CostLine, ...]
    unit_cost: Money
    fx: FxRate | None = None
    delivery_terms: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "price": str(self.price),
            "delivery_terms": self.delivery_terms,
            "fx": {
                "rate": str(self.fx.rate),
                "source": self.fx.source,
                "as_of": self.fx.as_of.isoformat(),
            }
            if self.fx
            else None,
            "lines": [line.as_json() for line in self.lines],
            "unit_cost_sgd": str(self.unit_cost.amount),
        }


def latest_rate(rates: list[FxRate], currency: str, on: date) -> FxRate | None:
    """The most recent recorded rate for the currency, on or before the day."""
    known = [r for r in rates if r.currency == currency.upper() and r.as_of <= on]
    return max(known, key=lambda r: r.as_of, default=None)


def landed(
    price: Decimal,
    currency: str,
    *,
    fx: FxRate | None = None,
    delivery_terms: str | None = None,
    config: dict[str, Any] | None = None,
    overrides: dict[str, Decimal] | None = None,
) -> Landed:
    """A unit price as a unit cost in SGD, with every step as a line.

    `overrides` replaces an import line's percentage for this price (a freight quote in
    hand), by the line's key. A price in SGD is not converted and carries no buffer.
    """
    currency = currency.upper()
    terms = (delivery_terms or "").upper() or None
    lines: list[CostLine] = []
    if currency == HOME:
        base = price.quantize(FOUR, ROUND_HALF_UP)
        lines.append(CostLine("price", "Supplier price", base, f"{price} SGD as quoted"))
    else:
        if fx is None or fx.currency != currency:
            raise NoFxRate(f"no FX rate is recorded for {currency}: record one before pricing")
        converted = (price * fx.rate).quantize(FOUR, ROUND_HALF_UP)
        lines.append(
            CostLine(
                "price",
                "Supplier price",
                converted,
                f"{price} {currency} x {fx.rate} ({fx.source}, {fx.as_of.isoformat()})",
            )
        )
        percent = buffer_percent(config)
        buffer = (converted * percent / 100).quantize(FOUR, ROUND_HALF_UP)
        lines.append(CostLine("fx_buffer", "FX buffer", buffer, f"{percent}% of {converted}"))
        base = converted + buffer
    if terms is not None:
        for line in import_lines(config):
            if terms not in line.applies_to:
                continue
            percent = (overrides or {}).get(line.key, line.percent)
            amount = (base * percent / 100).quantize(FOUR, ROUND_HALF_UP)
            lines.append(CostLine(line.key, line.label, amount, f"{percent}% of {base} ({terms})"))
    total = sum((line.amount for line in lines), Decimal(0))
    return Landed(
        currency=currency,
        price=price,
        lines=tuple(lines),
        unit_cost=Money(total.quantize(CENT, ROUND_HALF_UP)),
        fx=fx if currency != HOME else None,
        delivery_terms=terms,
    )


# --- GST --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GstRate:
    effective_from: date
    percent: Decimal


def gst_rates(config: dict[str, Any] | None = None) -> list[GstRate]:
    found = ((config if config is not None else settings()).get("gst") or {}).get("rates") or []
    return sorted(
        (
            GstRate(
                item["effective_from"]
                if isinstance(item["effective_from"], date)
                else date.fromisoformat(str(item["effective_from"])),
                Decimal(str(item["percent"])),
            )
            for item in found
        ),
        key=lambda rate: rate.effective_from,
    )


def gst_rate_on(day: date, rates: list[GstRate] | None = None) -> GstRate:
    """The rate in force on a day: the latest that had taken effect by then."""
    known = [
        rate for rate in (gst_rates() if rates is None else rates) if rate.effective_from <= day
    ]
    if not known:
        raise ValueError(f"no GST rate is configured for {day.isoformat()}")
    return known[-1]


@dataclass(frozen=True)
class Gst:
    rate: GstRate
    exclusive: Money
    gst: Money
    inclusive: Money = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "inclusive", Money(self.exclusive.amount + self.gst.amount))


def gst_on(exclusive: Money, priced_on: date, rates: list[GstRate] | None = None) -> Gst:
    """GST on a total held exclusive of it, at the rate in force when the bid was priced."""
    rate = gst_rate_on(priced_on, rates)
    amount = (exclusive.amount * rate.percent / 100).quantize(CENT, ROUND_HALF_UP)
    return Gst(rate, exclusive, Money(amount))

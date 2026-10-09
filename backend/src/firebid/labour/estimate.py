"""Labour hours and cost for a bill: baseline x multipliers x the trade's hourly rate.

Every factor stays visible. A line shows its baseline hours (quantity x the library
entry's man-hours per unit, with the entry's source), each multiplier it carries (value,
source, rationale, who confirmed it), the hours they come to, and the trade rate the cost
is worked at. A line with no library entry has no hours: it is listed, not guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from firebid.domain.values import Money
from firebid.labour.multipliers import Applied
from firebid.labour.productivity import Entry
from firebid.labour.rates import TradeRate

CENT = Decimal("0.01")


def _hours(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class BillLine:
    id: str
    reference: str
    description: str
    section: str  # the bill's heading for the line's system
    level: str | None
    unit: str
    quantity: Decimal


@dataclass(frozen=True)
class Portion:
    """The part of a line that is on one level. A line the bill rolls up over the building
    has one for each level its takeoff items are on, so that a level's multiplier reaches
    the labour on that level and no other."""

    level: str | None
    quantity: Decimal
    baseline_hours: Decimal
    multipliers: tuple[Applied, ...]


@dataclass(frozen=True)
class LabourLine:
    line: BillLine
    entry: Entry | None
    baseline_hours: Decimal | None
    multipliers: tuple[Applied, ...]
    factor: Decimal
    hours: Decimal | None
    rate: TradeRate | None
    cost: Money | None
    reason: str = ""  # why there are no hours
    # Empty for a line that is of one place: its own level, or no level at all.
    portions: tuple[Portion, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "line_id": self.line.id,
            "reference": self.line.reference,
            "description": self.line.description,
            "section": self.line.section,
            "level": self.line.level,
            "unit": self.line.unit,
            "quantity": str(self.line.quantity),
            "hours_per_unit": str(self.entry.hours) if self.entry else None,
            "productivity_source": self.entry.source if self.entry else None,
            "productivity_entry_id": self.entry.id if self.entry else None,
            "trade": self.entry.trade if self.entry else None,
            "baseline_hours": str(self.baseline_hours) if self.baseline_hours is not None else None,
            "multipliers": [m.as_json() for m in self.multipliers],
            "factor": str(self.factor),
            "hours": str(self.hours) if self.hours is not None else None,
            "hourly_rate": str(self.rate.hourly) if self.rate else None,
            "cost": str(self.cost.amount) if self.cost is not None else None,
            "reason": self.reason,
        }


def _factor(applied: Sequence[Applied]) -> Decimal:
    factor = Decimal(1)
    for multiplier in applied:
        factor *= multiplier.value
    return factor


def line_hours(
    line: BillLine,
    entry: Entry | None,
    applied: Sequence[Applied],
    rate: TradeRate | None,
    by_level: Sequence[tuple[str | None, Decimal, Sequence[Applied]]] | None = None,
) -> LabourLine:
    """One line: baseline hours, the multipliers one by one, the hours, and the cost.

    `by_level` is for a line rolled up over the building: the quantity on each level, with
    the multipliers that level carries. The line's hours are then the sum of its levels',
    and its factor what they come to over the baseline.
    """
    if entry is None:
        return LabourLine(
            line, None, None, (), Decimal(1), None, None, None, "no productivity entry"
        )
    baseline = line.quantity * entry.hours
    factor = _factor(applied)
    worked = baseline * factor
    portions: tuple[Portion, ...] = ()
    if by_level:
        portions = tuple(
            Portion(level, quantity, _hours(quantity * entry.hours), tuple(carried))
            for level, quantity, carried in by_level
        )
        factors = [_factor(carried) for _, _, carried in by_level]
        worked = sum(
            (q * entry.hours * f for (_, q, _), f in zip(by_level, factors, strict=True)),
            Decimal(0),
        )
        seen: dict[str, Applied] = {}
        for _, _, carried in by_level:
            for multiplier in carried:
                seen.setdefault(multiplier.key, multiplier)
        applied = list(seen.values())
        if len(set(factors)) == 1:
            factor = factors[0]
        else:
            factor = (worked / baseline).quantize(Decimal("0.0001")) if baseline else Decimal(1)
    hours = _hours(worked)
    cost = Money((hours * rate.hourly).quantize(CENT, ROUND_HALF_UP)) if rate else None
    return LabourLine(
        line=line,
        entry=entry,
        baseline_hours=_hours(baseline),
        multipliers=tuple(applied),
        factor=factor,
        hours=hours,
        rate=rate,
        cost=cost,
        reason="" if rate else f"no labour rate for the trade {entry.trade}",
        portions=portions,
    )


@dataclass
class Subtotal:
    key: str
    label: str
    baseline_hours: Decimal = Decimal("0.00")
    hours: Decimal = Decimal("0.00")
    cost: Decimal = Decimal("0.00")
    hourly_rate: Decimal | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "baseline_hours": str(self.baseline_hours),
            "hours": str(self.hours),
            "cost": str(self.cost),
            "hourly_rate": str(self.hourly_rate) if self.hourly_rate is not None else None,
        }


@dataclass
class Estimate:
    lines: list[LabourLine]
    by_section: list[Subtotal] = field(default_factory=list)
    by_trade: list[Subtotal] = field(default_factory=list)
    baseline_hours: Decimal = Decimal("0.00")
    hours: Decimal = Decimal("0.00")
    cost: Money = field(default_factory=Money.zero)
    without_hours: int = 0


def estimate(lines: list[LabourLine]) -> Estimate:
    """The lines with their totals: per system (the bill's sections) and by trade."""
    sections: dict[str, Subtotal] = {}
    trades: dict[str, Subtotal] = {}
    out = Estimate(lines=lines)
    for item in lines:
        if item.hours is None or item.baseline_hours is None:
            out.without_hours += 1
            continue
        cost = item.cost.amount if item.cost is not None else Decimal(0)
        section = sections.setdefault(
            item.line.section, Subtotal(item.line.section, item.line.section or "(no section)")
        )
        trade_key = item.entry.trade if item.entry else ""
        trade = trades.setdefault(
            trade_key,
            Subtotal(
                trade_key,
                item.rate.label if item.rate else trade_key,
                hourly_rate=item.rate.hourly if item.rate else None,
            ),
        )
        for total in (section, trade):
            total.baseline_hours += item.baseline_hours
            total.hours += item.hours
            total.cost += cost
        out.baseline_hours += item.baseline_hours
        out.hours += item.hours
        out.cost = Money(out.cost.amount + cost)
    out.by_section = list(sections.values())
    out.by_trade = sorted(trades.values(), key=lambda total: total.key)
    return out

"""The hourly labour rate, built up from the company's tables (FR-LAB-03).

A rate table has an effective date, and a bid takes the one in force on the day it is
priced. For each grade the table gives monthly wages, the foreign worker levy,
accommodation and transport; insurance (WICA) is a percentage of wages; overtime is the
share of hours worked at a premium; supervision is one supervisor to so many workers. Each
is a line of the build-up, in SGD an hour. A trade's rate is its crew's grades by their
share of the hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from firebid.labour.multipliers import settings

FOUR = Decimal("0.0001")
COMPONENTS = (
    ("wage", "Wages"),
    ("levy", "Foreign worker levy"),
    ("accommodation", "Accommodation"),
    ("transport", "Transport"),
    ("insurance", "Insurance (WICA)"),
    ("overtime", "Overtime premium"),
    ("supervision", "Supervision"),
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(FOUR, ROUND_HALF_UP)


@dataclass(frozen=True)
class Table:
    effective_from: date
    source: str
    hours: Decimal  # productive hours a month
    overtime_share: Decimal  # percent of hours
    overtime_premium: Decimal
    supervision_ratio: Decimal
    supervision_grade: str
    insurance_percent: Decimal
    grades: dict[str, dict[str, Any]]
    trades: dict[str, dict[str, Any]]


def tables(config: dict[str, Any] | None = None) -> list[Table]:
    found = (config if config is not None else settings()).get("rate_tables") or []
    out = []
    for item in found:
        effective = item["effective_from"]
        overtime = item.get("overtime") or {}
        supervision = item.get("supervision") or {}
        if not str(item.get("source") or "").strip():
            raise ValueError(f"the labour rate table from {effective} has no source")
        out.append(
            Table(
                effective_from=effective
                if isinstance(effective, date)
                else date.fromisoformat(str(effective)),
                source=str(item["source"]).strip(),
                hours=Decimal(str(item["productive_hours_per_month"])),
                overtime_share=Decimal(str(overtime.get("share_percent", 0))),
                overtime_premium=Decimal(str(overtime.get("premium", 1))),
                supervision_ratio=Decimal(str(supervision.get("ratio", 0))),
                supervision_grade=str(supervision.get("grade", "")),
                insurance_percent=Decimal(str(item.get("insurance_percent", 0))),
                grades=dict(item.get("grades") or {}),
                trades=dict(item.get("trades") or {}),
            )
        )
    return sorted(out, key=lambda table: table.effective_from)


def table_on(day: date, known: list[Table] | None = None) -> Table:
    """The table in force on a day: the latest that had taken effect by then."""
    found = [t for t in (tables() if known is None else known) if t.effective_from <= day]
    if not found:
        raise ValueError(f"no labour rate table is configured for {day.isoformat()}")
    return found[-1]


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    hourly: Decimal
    basis: str

    def as_json(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "hourly": str(self.hourly),
            "basis": self.basis,
        }


@dataclass(frozen=True)
class GradeRate:
    grade: str
    label: str
    components: tuple[Component, ...]
    hourly: Decimal

    def as_json(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "label": self.label,
            "components": [c.as_json() for c in self.components],
            "hourly": str(self.hourly),
        }


@dataclass(frozen=True)
class TradeRate:
    trade: str
    label: str
    crew: tuple[tuple[str, Decimal], ...]  # (grade, percent of hours)
    grades: tuple[GradeRate, ...]
    hourly: Decimal
    effective_from: date
    source: str

    def as_json(self) -> dict[str, Any]:
        return {
            "trade": self.trade,
            "label": self.label,
            "crew": [{"grade": grade, "percent": str(share)} for grade, share in self.crew],
            "grades": [grade.as_json() for grade in self.grades],
            "hourly": str(self.hourly),
            "effective_from": self.effective_from.isoformat(),
            "source": self.source,
        }


def _worker(table: Table, grade: str) -> list[Component]:
    """A grade's own hourly cost, before supervision."""
    values = table.grades[grade]
    wage = Decimal(str(values.get("wage", 0)))
    lines = []
    for key, label in COMPONENTS[:4]:
        monthly = Decimal(str(values.get(key, 0)))
        lines.append(
            Component(key, label, _q(monthly / table.hours), f"{monthly} a month / {table.hours} h")
        )
    insured = wage * table.insurance_percent / 100
    lines.append(
        Component(
            "insurance",
            "Insurance (WICA)",
            _q(insured / table.hours),
            f"{table.insurance_percent}% of wages {wage} / {table.hours} h",
        )
    )
    premium = wage / table.hours * table.overtime_share / 100 * (table.overtime_premium - 1)
    lines.append(
        Component(
            "overtime",
            "Overtime premium",
            _q(premium),
            f"{table.overtime_share}% of hours at {table.overtime_premium} x the hourly wage",
        )
    )
    return lines


def grade_rate(table: Table, grade: str) -> GradeRate:
    """A grade's hourly cost with every line: its own costs, and its share of a supervisor."""
    if grade not in table.grades:
        raise ValueError(f"the labour rate table has no grade {grade!r}")
    lines = _worker(table, grade)
    if (
        table.supervision_ratio > 0
        and table.supervision_grade in table.grades
        and grade != table.supervision_grade
    ):
        supervisor = sum((c.hourly for c in _worker(table, table.supervision_grade)), Decimal(0))
        lines.append(
            Component(
                "supervision",
                "Supervision",
                _q(supervisor / table.supervision_ratio),
                f"one supervisor at {supervisor} an hour to {table.supervision_ratio} workers",
            )
        )
    else:
        lines.append(Component("supervision", "Supervision", Decimal("0.0000"), "none"))
    return GradeRate(
        grade=grade,
        label=str(table.grades[grade].get("label") or grade),
        components=tuple(lines),
        hourly=_q(sum((c.hourly for c in lines), Decimal(0))),
    )


def trade_rate(trade: str, day: date, known: list[Table] | None = None) -> TradeRate:
    """A trade's hourly cost on a day: its crew's grades by their share of the hours."""
    table = table_on(day, known)
    if trade not in table.trades:
        raise ValueError(
            f"the labour rate table from {table.effective_from} has no trade {trade!r}"
        )
    crew = {
        str(grade): Decimal(str(share))
        for grade, share in (table.trades[trade].get("crew") or {}).items()
    }
    if sum(crew.values(), Decimal(0)) != 100:
        raise ValueError(f"the crew of {trade} does not add up to 100%")
    grades = tuple(grade_rate(table, grade) for grade in crew)
    hourly = sum((rate.hourly * crew[rate.grade] / 100 for rate in grades), Decimal(0))
    return TradeRate(
        trade=trade,
        label=str(table.trades[trade].get("label") or trade),
        crew=tuple(crew.items()),
        grades=grades,
        hourly=_q(hourly),
        effective_from=table.effective_from,
        source=table.source,
    )


def trades_on(day: date, known: list[Table] | None = None) -> list[TradeRate]:
    table = table_on(day, known)
    return [trade_rate(trade, day, known) for trade in table.trades]

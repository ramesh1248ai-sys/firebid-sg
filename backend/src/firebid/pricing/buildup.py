"""The cost build-up: every component of a bid's price as its own visible line (FR-CST-06).

Seventeen components, in the order an estimator reads them:

* **from the priced bill:** materials, fittings, valves and equipment (each the total of the
  bill's lines in its groups) and wastage (each line's allowance quantity at its rate);
* **entered by an estimator:** labour (until P2-05 estimates it), supervision, access
  equipment, testing and commissioning, transport, subcontract, site overheads,
  preliminaries, insurance, bonds, contingency and margin.

An entered line is a lump sum or a percentage of a base, and carries the name of the
estimator who entered it and when. The platform never fills one in: a component nobody has
entered is "not set" and adds nothing (FR-CST-09). A percentage is of one of three bases:

* `direct`: everything up to and including subcontract and wastage;
* `cost`: direct plus site overheads, preliminaries, insurance and bonds;
* `cost_with_contingency`: cost plus contingency (what margin is usually on).

Totals are exclusive of GST; GST is shown separately at the rate in force on the day the
bid is priced (`pricing.landed.gst_on`).

Arithmetic is Decimal, rounded half-up to the cent per line (guardrail 3).

Pure: the bill's priced lines and the entered lines in; the build-up out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from firebid.domain.values import Money
from firebid.pricing import landed

CENT = Decimal("0.01")
ZERO = Money(Decimal("0.00"))

# (key, label, how it is reached)
COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("materials", "Materials", "calculated"),
    ("fittings", "Fittings", "calculated"),
    ("valves", "Valves", "calculated"),
    ("equipment", "Equipment", "calculated"),
    ("wastage", "Wastage", "calculated"),
    ("labour", "Labour", "entered"),
    ("supervision", "Supervision", "entered"),
    ("access_equipment", "Access equipment", "entered"),
    ("testing_commissioning", "Testing and commissioning", "entered"),
    ("transport", "Transport", "entered"),
    ("subcontract", "Subcontract", "entered"),
    ("site_overheads", "Site overheads", "entered"),
    ("preliminaries", "Preliminaries", "entered"),
    ("insurance", "Insurance", "entered"),
    ("bonds", "Bonds", "entered"),
    ("contingency", "Contingency", "entered"),
    ("margin", "Margin", "entered"),
)
KEYS = tuple(key for key, _, _ in COMPONENTS)
ENTERED = tuple(key for key, _, how in COMPONENTS if how == "entered")
DIRECT = KEYS[: KEYS.index("subcontract") + 1]
INDIRECT = ("site_overheads", "preliminaries", "insurance", "bonds")
BASES = {
    "direct": "direct cost",
    "cost": "cost",
    "cost_with_contingency": "cost with contingency",
}
# Which base each percentage may be of: a line is never a percentage of a base it is in.
ALLOWED_BASES: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(INDIRECT, ("direct",)),
    "contingency": ("direct", "cost"),
    "margin": ("direct", "cost", "cost_with_contingency"),
}
# The bill's groups that are not materials, by the words of their heading.
GROUPS = (("fittings", "fitting"), ("valves", "valve"), ("equipment", "equipment"))


@dataclass(frozen=True)
class BillLine:
    """One line of the priced bill, as the build-up takes it."""

    reference: str  # item number or description
    group: str  # the bill's group heading
    quantity: Decimal
    amount: Money | None  # None: unpriced
    unit_rate: Money | None = None
    allowance_percent: Decimal | None = None
    allowance: bool = False  # an estimator's lump sum or provisional sum, not a rate


@dataclass(frozen=True)
class Entered:
    """What an estimator entered for a component: a lump sum, or a percentage of a base."""

    component: str
    basis: str  # lump_sum | percentage
    entered_by: str
    entered_on: date
    amount: Money | None = None
    percent: Decimal | None = None
    base: str | None = None
    note: str | None = None

    def check(self) -> None:
        if self.component not in ENTERED:
            raise ValueError(f"{self.component!r} is not a component an estimator enters")
        if not self.entered_by.strip():
            raise ValueError("an entered cost carries the name of the estimator who entered it")
        if self.basis == "lump_sum":
            if self.amount is None or self.amount.amount < 0:
                raise ValueError("a lump sum is an amount of money, not negative")
        elif self.basis == "percentage":
            if self.percent is None or not 0 <= self.percent <= 100:
                raise ValueError("a percentage is between 0 and 100")
            allowed = ALLOWED_BASES.get(self.component, ("direct",))
            if self.base not in allowed:
                raise ValueError(
                    f"{self.component} is a percentage of "
                    + " or ".join(BASES[base] for base in allowed)
                )
            if self.component in DIRECT:
                raise ValueError(f"{self.component} is part of direct cost: enter it as a lump sum")
        else:
            raise ValueError("a basis is 'lump_sum' or 'percentage'")


@dataclass(frozen=True)
class Calculated:
    """A component worked out elsewhere and handed to the build-up with its basis: labour,
    from the productivity library and the labour rate tables (P2-05)."""

    component: str
    amount: Money
    detail: str
    source: str


@dataclass(frozen=True)
class Line:
    component: str
    label: str
    basis: str  # calculated | lump_sum | percentage | not set
    detail: str
    source: str
    amount: Money | None

    def as_json(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "label": self.label,
            "basis": self.basis,
            "detail": self.detail,
            "source": self.source,
            "amount": str(self.amount.amount) if self.amount is not None else None,
        }


@dataclass
class BuildUp:
    lines: list[Line]
    direct: Money
    cost: Money
    cost_with_contingency: Money
    total: Money  # exclusive of GST
    gst: landed.Gst
    priced_on: date
    unpriced_lines: int
    not_set: list[str] = field(default_factory=list)

    def by_component(self) -> dict[str, Money | None]:
        return {line.component: line.amount for line in self.lines}


def component_of(group: str) -> str:
    words = group.lower()
    return next((key for key, word in GROUPS if word in words), "materials")


def _money(value: Decimal) -> Money:
    return Money(value.quantize(CENT, ROUND_HALF_UP))


def build(
    bill: list[BillLine],
    entered: list[Entered],
    priced_on: date,
    gst_rates: list[landed.GstRate] | None = None,
    calculated: list[Calculated] | None = None,
) -> BuildUp:
    """The build-up of a priced bill and what estimators entered, as of a pricing date.

    `calculated` gives a component its amount where no estimator has entered one: an
    estimator's own figure stands, and says what the calculation came to.
    """
    for given in entered:
        given.check()
    stated = {item.component: item for item in entered}
    worked = {item.component: item for item in calculated or []}
    amounts: dict[str, Money | None] = {}
    lines: list[Line] = []

    def total_of(keys: tuple[str, ...]) -> Decimal:
        return sum(
            (amount.amount for key in keys if (amount := amounts.get(key)) is not None), Decimal(0)
        )

    labels = {key: label for key, label, _ in COMPONENTS}
    priced = [line for line in bill if line.amount is not None]
    for key in ("materials", "fittings", "valves", "equipment"):
        mine = [line for line in priced if component_of(line.group) == key]
        amount = _money(sum((line.amount.amount for line in mine if line.amount), Decimal(0)))
        allowances = sum(1 for line in mine if line.allowance)
        amounts[key] = amount
        lines.append(
            Line(
                key,
                labels[key],
                "calculated",
                f"{len(mine)} priced bill line(s)"
                + (f", {allowances} of them an estimator's allowance" if allowances else ""),
                "the priced bill of quantities",
                amount,
            )
        )
    wasted = [
        line
        for line in priced
        if line.allowance_percent and line.unit_rate is not None and not line.allowance
    ]
    wastage = _money(
        sum(
            (
                line.quantity * line.allowance_percent / 100 * line.unit_rate.amount
                for line in wasted
                if line.allowance_percent is not None and line.unit_rate is not None
            ),
            Decimal(0),
        )
    )
    amounts["wastage"] = wastage
    lines.append(
        Line(
            "wastage",
            labels["wastage"],
            "calculated",
            f"each line's allowance quantity at its rate, {len(wasted)} line(s)",
            "the bill's allowance percentages (measurement rules)",
            wastage,
        )
    )

    bases: dict[str, Decimal] = {}
    for key in ENTERED:
        if key == "site_overheads":
            bases["direct"] = total_of(DIRECT)
        if key == "contingency":
            bases["cost"] = bases["direct"] + total_of(INDIRECT)
        if key == "margin":
            bases["cost_with_contingency"] = bases["cost"] + total_of(("contingency",))
        item = stated.get(key)
        found = worked.get(key)
        if item is None and found is not None:
            amounts[key] = found.amount
            lines.append(
                Line(key, labels[key], "calculated", found.detail, found.source, found.amount)
            )
            continue
        if item is None:
            amounts[key] = None
            lines.append(
                Line(key, labels[key], "not set", "nobody has entered it", "not set", None)
            )
            continue
        source = f"entered by {item.entered_by} on {item.entered_on.isoformat()}" + (
            f": {item.note}" if item.note else ""
        )
        if found is not None:
            source += f" (in place of the calculated {found.amount.amount})"
        if item.basis == "lump_sum" and item.amount is not None:
            amounts[key] = item.amount
            lines.append(Line(key, labels[key], "lump_sum", "a lump sum", source, item.amount))
            continue
        base = bases[str(item.base)]
        amount = _money(base * (item.percent or Decimal(0)) / 100)
        amounts[key] = amount
        lines.append(
            Line(
                key,
                labels[key],
                "percentage",
                f"{item.percent}% of {BASES[str(item.base)]} {_money(base).amount}",
                source,
                amount,
            )
        )
    direct = _money(total_of(DIRECT))
    cost = _money(direct.amount + total_of(INDIRECT))
    with_contingency = _money(cost.amount + total_of(("contingency",)))
    total = _money(with_contingency.amount + total_of(("margin",)))
    ordered = sorted(lines, key=lambda line: KEYS.index(line.component))
    return BuildUp(
        lines=ordered,
        direct=direct,
        cost=cost,
        cost_with_contingency=with_contingency,
        total=total,
        gst=landed.gst_on(total, priced_on, gst_rates),
        priced_on=priced_on,
        unpriced_lines=sum(1 for line in bill if line.amount is None),
        not_set=[line.component for line in ordered if line.basis == "not set"],
    )

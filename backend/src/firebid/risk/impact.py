"""What a risk could cost (FR-RSK-04).

An execution risk that a labour multiplier describes is valued with the labour engine
(P2-05): the hours it reaches, times what the multiplier adds, at the trades' rates. The
figure is an allowance the estimator accepts or adjusts with a reason. A risk no engine can
value says so: it is flagged, and the estimator enters a figure or none.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")


@dataclass(frozen=True)
class LabourReach:
    """The labour a risk reaches: the hours and their cost, before the multiplier."""

    lines: int
    hours: Decimal
    cost: Decimal


@dataclass(frozen=True)
class Impact:
    method: str  # labour_multiplier | already_priced | not_computed
    basis: str
    cost: Decimal | None = None  # SGD
    hours: Decimal | None = None  # man-hours: the programme side
    multiplier: str | None = None
    factor: Decimal | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "basis": self.basis,
            "cost": str(self.cost) if self.cost is not None else None,
            "hours": str(self.hours) if self.hours is not None else None,
            "multiplier": self.multiplier,
            "factor": str(self.factor) if self.factor is not None else None,
        }


def labour_impact(
    reach: LabourReach,
    multiplier: str,
    label: str,
    factor: Decimal,
    *,
    already_applied: bool,
    scope: str,
) -> Impact:
    """The extra labour a multiplier brings on the hours it reaches."""
    if reach.lines == 0 or reach.hours == 0:
        return Impact(
            "not_computed",
            f'no labour is estimated on {scope} yet, so the effect of "{label}" cannot be '
            "worked out",
            multiplier=multiplier,
            factor=factor,
        )
    extra = factor - 1
    hours = (reach.hours * extra).quantize(CENT, ROUND_HALF_UP)
    cost = (reach.cost * extra).quantize(CENT, ROUND_HALF_UP)
    if already_applied:
        # The estimate carries it: the hours given are those before it was applied.
        return Impact(
            "already_priced",
            f'"{label}" (x {factor}) is confirmed in the labour estimate for {scope}: '
            f"{hours} man-hours, SGD {cost}, are already in the labour cost",
            cost=Decimal("0.00"),
            hours=Decimal("0.00"),
            multiplier=multiplier,
            factor=factor,
        )
    return Impact(
        "labour_multiplier",
        f'"{label}" (x {factor}) on {reach.hours} man-hours over {reach.lines} bill '
        f"line(s) on {scope}: {hours} man-hours more, SGD {cost} at the trades' rates",
        cost=cost,
        hours=hours,
        multiplier=multiplier,
        factor=factor,
    )


def not_computed(reason: str) -> Impact:
    return Impact("not_computed", reason)

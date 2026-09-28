"""A BOQ line's price comes from a rate entry, or there is none (guardrail 4, FR-CST-01).

`price_from` is the only way a price is worked out: it takes the rate entry, never a number,
and gives the rate and amount to store with the entry's ID. Nothing without an entry is a
price; the database refuses one too (migration 0026).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from firebid.domain.values import Money


class Unsourced(ValueError):
    """A price with no rate entry behind it."""


class RateSource(Protocol):
    """What a price needs from its rate entry."""

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def unit_rate(self) -> Money: ...


@dataclass(frozen=True)
class Price:
    rate_id: uuid.UUID
    unit_rate: Money
    amount: Money


def price_from(entry: RateSource | None, quantity: Decimal) -> Price:
    """The rate and amount for `quantity` from `entry`: quantity x rate, half-up to the cent."""
    if entry is None or getattr(entry, "id", None) is None:
        raise Unsourced("a price needs a rate library entry: the line stays unpriced")
    if not isinstance(entry.unit_rate, Money):
        raise Unsourced("a rate entry's rate is Money")
    return Price(entry.id, entry.unit_rate, entry.unit_rate.times(quantity))

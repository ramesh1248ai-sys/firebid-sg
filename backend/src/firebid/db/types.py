"""SQLAlchemy column types for the domain value types, so units and money never leak as raw
numbers (project-context conventions)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Dialect, Numeric
from sqlalchemy.types import TypeDecorator

from firebid.domain.values import LengthMm, Money


class LengthMmType(TypeDecorator[LengthMm]):
    """A length stored as whole millimetres."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: LengthMm | None, dialect: Dialect) -> int | None:
        return None if value is None else value.mm

    def process_result_value(self, value: Any, dialect: Dialect) -> LengthMm | None:
        return None if value is None else LengthMm(int(value))


class MoneyType(TypeDecorator[Money]):
    """An SGD amount stored as numeric(14, 2)."""

    impl = Numeric(14, 2)
    cache_ok = True

    def process_bind_param(self, value: Money | None, dialect: Dialect) -> Decimal | None:
        return None if value is None else value.amount

    def process_result_value(self, value: Any, dialect: Dialect) -> Money | None:
        return None if value is None else Money.of(Decimal(value))

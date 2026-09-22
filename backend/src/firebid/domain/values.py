"""Value types shared by every module.

Units and money follow project-context conventions: lengths are integer millimetres and
displayed in metres; money is Decimal SGD at 2 dp, rounded half-up at line level.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

MM_PER_METRE = 1000
_METRE_EXPONENT = Decimal("0.001")
_MONEY_EXPONENT = Decimal("0.01")


@dataclass(frozen=True, order=True)
class LengthMm:
    """A length in whole millimetres. Differences may be negative."""

    mm: int

    def __post_init__(self) -> None:
        if not isinstance(self.mm, int) or isinstance(self.mm, bool):
            raise TypeError("LengthMm takes whole millimetres as int")

    @classmethod
    def from_metres(cls, metres: Decimal | int | str) -> LengthMm:
        value = Decimal(metres) if not isinstance(metres, Decimal) else metres
        if not value.is_finite():
            raise ValueError("length must be finite")
        return cls(int((value * MM_PER_METRE).quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    def to_metres(self) -> Decimal:
        return (Decimal(self.mm) / MM_PER_METRE).quantize(_METRE_EXPONENT)

    def __add__(self, other: LengthMm) -> LengthMm:
        return LengthMm(self.mm + other.mm)

    def __sub__(self, other: LengthMm) -> LengthMm:
        return LengthMm(self.mm - other.mm)

    def __mul__(self, factor: int) -> LengthMm:
        return LengthMm(self.mm * factor)

    def __neg__(self) -> LengthMm:
        return LengthMm(-self.mm)

    def __abs__(self) -> LengthMm:
        return LengthMm(abs(self.mm))

    def __str__(self) -> str:
        return f"{self.to_metres()} m"


@dataclass(frozen=True, order=True)
class Money:
    """An amount in SGD, held at exactly 2 dp. Build it with :meth:`of`."""

    amount: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise TypeError("Money takes a finite Decimal")
        if self.amount.as_tuple().exponent != -2:
            raise ValueError("Money must be quantized to 2 dp; use Money.of()")

    @classmethod
    def of(cls, value: Decimal | int | str) -> Money:
        decimal_value = Decimal(value) if not isinstance(value, Decimal) else value
        if not decimal_value.is_finite():
            raise ValueError("money must be finite")
        return cls(decimal_value.quantize(_MONEY_EXPONENT, rounding=ROUND_HALF_UP))

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal("0.00"))

    def times(self, factor: Decimal | int) -> Money:
        """Multiply and round half-up, e.g. a quantity times a unit rate."""
        return Money.of(
            self.amount * (Decimal(factor) if not isinstance(factor, Decimal) else factor)
        )

    def __add__(self, other: Money) -> Money:
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount - other.amount)

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def __str__(self) -> str:
        return f"SGD {self.amount}"


@dataclass(frozen=True, order=True)
class Confidence:
    """A calibrated probability between 0 and 1."""

    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def __str__(self) -> str:
        return f"{self.value:.2f}"


class ExtractionMethod(StrEnum):
    """How an object was found. Recorded on every detection (FR-VIS-01)."""

    CAD_ENTITY = "cad_entity"
    PDF_VECTOR = "pdf_vector"
    OCR = "ocr"
    VISION = "vision"
    RULE = "rule"
    MANUAL = "manual"


class CalculationMethod(StrEnum):
    """How a quantity was produced (FR-QTO-09)."""

    COUNT = "count"
    CENTRELINE_LENGTH = "centreline_length"
    RULE_DERIVED = "rule_derived"
    ALLOWANCE = "allowance"
    MANUAL_MEASURE = "manual_measure"

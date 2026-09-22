"""Value type behaviour, including Hypothesis property tests for units and money."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from firebid.domain.values import Confidence, LengthMm, Money

lengths = st.integers(min_value=-10_000_000, max_value=10_000_000).map(LengthMm)
money_amounts = st.decimals(
    min_value=Decimal("-1000000"), max_value=Decimal("1000000"), places=2, allow_nan=False
).map(Money.of)


class TestLengthMm:
    @given(lengths)
    def test_metres_round_trip(self, length: LengthMm) -> None:
        assert LengthMm.from_metres(length.to_metres()) == length

    @given(lengths, lengths, lengths)
    def test_addition_is_associative(self, a: LengthMm, b: LengthMm, c: LengthMm) -> None:
        assert (a + b) + c == a + (b + c)

    @given(lengths, lengths)
    def test_difference_reverses_addition(self, a: LengthMm, b: LengthMm) -> None:
        assert (a + b) - b == a

    @pytest.mark.parametrize(
        ("metres", "mm"),
        # Half-up rounds away from zero, matching Money (-0.005 -> -0.01).
        [("1", 1000), ("0.0005", 1), ("0.0004", 0), ("128.4", 128_400), ("-1.2345", -1235)],
    )
    def test_from_metres_rounds_half_up(self, metres: str, mm: int) -> None:
        assert LengthMm.from_metres(metres).mm == mm

    def test_display_uses_metres_at_three_decimals(self) -> None:
        assert str(LengthMm(128_400)) == "128.400 m"

    def test_float_millimetres_are_refused(self) -> None:
        with pytest.raises(TypeError):
            LengthMm(1.5)  # type: ignore[arg-type]


class TestMoney:
    @given(money_amounts)
    def test_always_two_decimal_places(self, amount: Money) -> None:
        assert amount.amount.as_tuple().exponent == -2

    @given(money_amounts, money_amounts)
    def test_addition_commutes(self, a: Money, b: Money) -> None:
        assert a + b == b + a

    @given(money_amounts)
    def test_doubling_equals_adding_to_itself(self, a: Money) -> None:
        assert a.times(2) == a + a

    @given(money_amounts, money_amounts)
    def test_subtraction_reverses_addition(self, a: Money, b: Money) -> None:
        assert (a + b) - b == a

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("0.005", "0.01"), ("0.004", "0.00"), ("-0.005", "-0.01"), ("12.345", "12.35")],
    )
    def test_rounds_half_up(self, value: str, expected: str) -> None:
        assert Money.of(value).amount == Decimal(expected)

    def test_unquantized_decimal_is_refused(self) -> None:
        with pytest.raises(ValueError, match="2 dp"):
            Money(Decimal("1.234"))

    def test_line_amount_rounds_once(self) -> None:
        # 128.4 m at SGD 12.35/m
        assert Money.of("12.35").times(Decimal("128.4")) == Money.of("1585.74")

    def test_display(self) -> None:
        assert str(Money.of("1585.74")) == "SGD 1585.74"


class TestConfidence:
    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_accepts_probabilities(self, value: float) -> None:
        assert Confidence(value).value == value

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_refuses_values_outside_the_range(self, value: float) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            Confidence(value)

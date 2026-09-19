from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderType


def test_money_arithmetic_is_exact() -> None:
    total = Money.of("0.1") + Money.of("0.2")

    assert total == Money.of("0.3")


def test_money_subtraction_negation_and_scaling() -> None:
    assert Money.of(10) - Money.of(4) == Money.of(6)
    assert -Money.of(5) == Money.of(-5)
    assert Money.of("2.50").times(3) == Money.of("7.5")


def test_money_orders_by_amount() -> None:
    assert Money.of(1) < Money.of(2)
    assert max(Money.of(1), Money.of(3)) == Money.of(3)


def test_zero() -> None:
    assert Money.zero() == Money.of(0)


@pytest.mark.parametrize("bad", [0.1, None, True])
def test_of_rejects_floats_and_non_numbers(bad: object) -> None:
    with pytest.raises(TypeError):
        Money.of(bad)  # type: ignore[arg-type]


def test_constructor_rejects_a_float_amount() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
def test_money_must_be_finite(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        Money(bad)


def test_market_and_ioc_order_types_are_unrepresentable() -> None:
    assert {member.value for member in OrderType} == {"LIMIT", "STOPLOSS_LIMIT"}
    with pytest.raises(ValueError):
        OrderType("MARKET")

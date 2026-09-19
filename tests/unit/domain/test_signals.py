from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind

TS = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _signal(**overrides: object) -> Signal:
    base = Signal(
        strategy_run_id="run-1",
        instrument_id="NSE:1001",
        kind=SignalKind.ENTRY,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=Money.of("100"),
        ts=TS,
        reason="crossed up",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_a_signal_is_an_immutable_value() -> None:
    signal = _signal()
    assert signal == _signal()
    with pytest.raises(AttributeError):
        signal.quantity = 5  # type: ignore[misc]


@pytest.mark.parametrize("forbidden", ["MARKET", "IOC", "LIMIT"])
def test_an_order_type_that_is_not_an_order_type_cannot_be_expressed(forbidden: str) -> None:
    # Even the legal-looking string "LIMIT" is refused: only the enum member is a type.
    with pytest.raises(TypeError, match="order_type"):
        _signal(order_type=forbidden)


def test_the_order_type_enum_has_no_market_or_ioc() -> None:
    assert {member.name for member in OrderType} == {"LIMIT", "STOPLOSS_LIMIT"}


@pytest.mark.parametrize("field", ["kind", "side"])
def test_raw_strings_for_enums_are_refused(field: str) -> None:
    with pytest.raises(TypeError, match=field):
        _signal(**{field: "BUY"})


def test_a_stop_loss_signal_needs_a_trigger_and_a_plain_limit_refuses_one() -> None:
    stop = _signal(order_type=OrderType.STOPLOSS_LIMIT, trigger_price=Money.of("99"))
    assert stop.trigger_price == Money.of("99")
    with pytest.raises(ValueError, match="trigger"):
        _signal(order_type=OrderType.STOPLOSS_LIMIT)
    with pytest.raises(ValueError, match="trigger"):
        _signal(order_type=OrderType.STOPLOSS_LIMIT, trigger_price=Money.zero())
    with pytest.raises(ValueError, match="trigger"):
        _signal(trigger_price=Money.of("99"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"quantity": 0}, "quantity"),
        ({"quantity": -3}, "quantity"),
        ({"limit_price": Money.zero()}, "price"),
        ({"limit_price": Money.of("-1")}, "price"),
        ({"reason": "  "}, "why"),
        ({"strategy_run_id": ""}, "run"),
        ({"instrument_id": ""}, "instrument"),
        ({"ts": datetime(2026, 1, 5, 4, 0)}, "UTC"),
        (
            {"ts": datetime(2026, 1, 5, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))},
            "UTC",
        ),
    ],
)
def test_invalid_signals_are_refused(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _signal(**overrides)

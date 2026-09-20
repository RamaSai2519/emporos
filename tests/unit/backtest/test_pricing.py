"""EM-102: the risk-gate seam and the minimal signal->order pricing (Phase 12 owns the real one)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.backtest.pricing import GateRejection, MarketableLimitPricing, PassThroughGate
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from tests.support.strategies import INSTRUMENT, T0, make_signal


class FixedTick:
    def __init__(self, tick: str = "0.05") -> None:
        self._tick = Money.of(tick)

    def tick_size(self, instrument_id: str) -> Money:
        return self._tick


def pricing(bps: str = "5", tick: str = "0.05") -> MarketableLimitPricing:
    return MarketableLimitPricing(Decimal(bps), FixedTick(tick))


class TestGate:
    def test_the_pass_through_gate_returns_the_very_same_signal(self) -> None:
        signal = make_signal()
        assert PassThroughGate().review(signal) is signal
        assert PassThroughGate.name == "none"

    def test_a_gate_can_say_no(self) -> None:
        assert GateRejection("too big").reason == "too big"


class TestMarketableLimitPricing:
    def test_a_buy_is_priced_above_the_signal_and_rounded_up_to_the_tick(self) -> None:
        # 100 * 1.0005 = 100.05 exactly on a 0.05 tick
        order = pricing().order_for(make_signal(side=OrderSide.BUY, price="100"), "tag-1")
        assert order.limit_price == Money.of("100.05")
        assert (order.order_type, order.tag, order.quantity) == (OrderType.LIMIT, "tag-1", 10)

    def test_a_buy_that_lands_between_ticks_rounds_toward_the_market(self) -> None:
        # 1234.57 * 1.0005 = 1235.187285 -> the next tick up is 1235.20
        assert pricing().order_for(make_signal(price="1234.57"), "t").limit_price == Money.of(
            "1235.20"
        )

    def test_a_sell_is_priced_below_the_signal_and_rounded_down(self) -> None:
        signal = make_signal(kind=SignalKind.EXIT, side=OrderSide.SELL, price="1234.57")
        # 1234.57 * 0.9995 = 1233.952715 -> 1233.95
        assert pricing().order_for(signal, "t").limit_price == Money.of("1233.95")

    def test_zero_buffer_only_rounds_to_the_tick(self) -> None:
        assert pricing("0").order_for(make_signal(price="100.02"), "t").limit_price == Money.of(
            "100.05"
        )

    def test_a_stop_limit_keeps_its_prices_and_only_snaps_to_the_tick(self) -> None:
        signal = Signal(
            "run-test-1", INSTRUMENT, SignalKind.EXIT, OrderSide.SELL, OrderType.STOPLOSS_LIMIT,
            10, Money.of("97.02"), T0, "stop", trigger_price=Money.of("98.01"),
        )  # fmt: skip

        order = pricing().order_for(signal, "s")

        assert (order.limit_price, order.trigger_price) == (Money.of("97.00"), Money.of("98.00"))
        assert order.order_type is OrderType.STOPLOSS_LIMIT

    def test_a_negative_buffer_is_refused(self) -> None:
        with pytest.raises(ValueError):
            pricing("-1")

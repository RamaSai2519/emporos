"""EM-102: the bar fill model's policies, each on its own."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.backtest.fill_model import (
    AtLimitPrice,
    BarFillModel,
    BarParticipation,
    GapAwareSlippage,
    ThroughBar,
    TouchBar,
    TouchTrigger,
)
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from tests.support.backtest import ohlc

BUY, SELL = OrderSide.BUY, OrderSide.SELL


def m(value: str) -> Money:
    return Money.of(value)


class TestCrossing:
    # bar range 98..102
    @pytest.mark.parametrize(
        ("side", "limit", "through", "touch"),
        [
            (BUY, "99", True, True),  # traded well below: fills either way
            (BUY, "98.01", True, True),
            (BUY, "98", False, True),  # the low only TOUCHES the limit
            (BUY, "97.99", False, False),  # never got down to it
            (SELL, "101", True, True),
            (SELL, "101.99", True, True),
            (SELL, "102", False, True),  # the high only touches
            (SELL, "102.01", False, False),
        ],
    )
    def test_through_needs_a_strict_cross_and_touch_accepts_equality(
        self, side: OrderSide, limit: str, through: bool, touch: bool
    ) -> None:
        bar = ohlc("100", "102", "98", "100")

        assert ThroughBar().crossed(side, m(limit), bar) is through
        assert TouchBar().crossed(side, m(limit), bar) is touch


class TestTrigger:
    @pytest.mark.parametrize(
        ("side", "trigger", "expected"),
        [
            (SELL, "98", True),  # the low reaches it
            (SELL, "97.99", False),
            (BUY, "102", True),
            (BUY, "102.01", False),
        ],
    )
    def test_a_stop_triggers_when_the_range_reaches_it(
        self, side: OrderSide, trigger: str, expected: bool
    ) -> None:
        assert (
            TouchTrigger().triggered(side, m(trigger), ohlc("100", "102", "98", "100")) is expected
        )


class TestPrice:
    def test_the_default_is_the_orders_own_limit(self) -> None:
        bar = ohlc("90", "102", "88", "100")  # it gapped far below a buy limit of 99
        assert AtLimitPrice().at(BUY, m("99"), bar) == m("99")

    def test_slippage_fills_at_a_better_open_then_moves_against_the_order(self) -> None:
        bar = ohlc("90", "102", "88", "100")
        # open 90 is better than the 99 limit; 10 bps against a buyer: 90 * 1.001 = 90.09
        assert GapAwareSlippage(Decimal(10)).at(BUY, m("99"), bar) == m("90.09")

    def test_slippage_never_takes_a_price_worse_than_the_limit(self) -> None:
        bar = ohlc("98.99", "102", "98", "100")
        # open 98.99 * 1.01 = 99.9799 would exceed the 99 limit: clamped to the limit
        assert GapAwareSlippage(Decimal(100)).at(BUY, m("99"), bar) == m("99")

    def test_a_seller_is_moved_down_and_clamped_at_the_limit(self) -> None:
        gapped_up = ohlc("110", "112", "108", "111")
        assert GapAwareSlippage(Decimal(10)).at(SELL, m("100"), gapped_up) == m("109.89")
        barely = ohlc("100.10", "102", "99", "100")
        assert GapAwareSlippage(Decimal(100)).at(SELL, m("100"), barely) == m("100")

    def test_rounding_is_against_the_order(self) -> None:
        bar = ohlc("100.001", "102", "99", "100")
        # 100.001 * 1.001 = 100.101001 -> a buyer pays the rounded-UP paisa
        assert GapAwareSlippage(Decimal(10)).at(BUY, m("101"), bar) == m("100.11")

    def test_negative_slippage_is_refused(self) -> None:
        with pytest.raises(ValueError):
            GapAwareSlippage(Decimal(-1))


class TestLiquidity:
    def test_capacity_is_the_floor_of_the_share_of_volume(self) -> None:
        assert BarParticipation(Decimal("0.1")).capacity(ohlc("1", "1", "1", "1", volume=999)) == 99

    def test_no_volume_means_no_capacity(self) -> None:
        assert BarParticipation(Decimal("0.5")).capacity(ohlc("1", "1", "1", "1", volume=0)) == 0

    @pytest.mark.parametrize("fraction", ["0", "-0.1", "1.01"])
    def test_participation_must_be_in_zero_to_one(self, fraction: str) -> None:
        with pytest.raises(ValueError):
            BarParticipation(Decimal(fraction))


class TestComposedModel:
    def test_defaults_are_the_conservative_ones(self) -> None:
        model = BarFillModel()
        bar = ohlc("100", "102", "98", "100", volume=1000)

        assert model.capacity(bar) == 100  # 10% of the bar
        assert model.price_if_filled(BUY, m("98"), bar) is None  # touching is not enough
        assert model.price_if_filled(BUY, m("98.05"), bar) == m("98.05")  # at its own limit

    def test_a_partial_bar_fills_nothing_and_triggers_nothing(self) -> None:
        model = BarFillModel()
        bar = ohlc("100", "102", "90", "100", partial=True)

        assert model.capacity(bar) == 0
        assert model.price_if_filled(BUY, m("99"), bar) is None
        assert model.triggers(SELL, m("95"), bar) is False

    def test_policies_are_swappable(self) -> None:
        model = BarFillModel(crossing=TouchBar(), liquidity=BarParticipation(Decimal(1)))
        bar = ohlc("100", "102", "98", "100", volume=1000)

        assert model.capacity(bar) == 1000
        assert model.price_if_filled(BUY, m("98"), bar) == m("98")

"""EM-102: the simulated broker. Look-ahead rules are tested where they are enforced."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.backtest.broker import (
    DuplicateTagError,
    OrderNotOpenError,
    SimulatedBroker,
    UnknownOrderError,
)
from emporos.backtest.fill_model import BarFillModel, BarParticipation, GapAwareSlippage, TouchBar
from emporos.backtest.orders import FillReason, SimOrderRequest
from emporos.backtest.rejects import NeverReject, SeededRejectRate
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdateStatus as S
from emporos.domain.orders import OrderSide, OrderType
from tests.support.backtest import BrokerRig, limit_order, ohlc, stop_order
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0

BUY, SELL = OrderSide.BUY, OrderSide.SELL


def prices(events: list) -> list[str]:
    return [str(e.fill.price.amount) for e in events if e.fill]


class TestAnOrderCannotTradeOnTheBarItWasPlacedOn:
    def test_a_crossing_bar_after_the_order_fills_it_and_the_bar_it_was_placed_on_does_not(
        self,
    ) -> None:
        rig = BrokerRig()
        bar_t = ohlc("100", "101", "97", "99", minutes=0)  # this bar dips well below 98
        rig.step(bar_t)  # the strategy sees bar t at its close...
        rig.broker.submit(limit_order(BUY, "98"))  # ...and answers with a buy at 98

        # Had the broker looked at bar t it would fill (low 97 < 98). It must not.
        assert rig.broker.open_orders()[0].filled == 0

        events = rig.step(ohlc("99", "100", "97.5", "98.5", minutes=5))  # bar t+1 crosses 98

        assert prices(events) == ["98"]
        assert events[0].fill.ts == T0 + timedelta(minutes=10)  # the fill time is bar t+1's close

    def test_an_order_placed_before_a_bar_opens_can_fill_on_that_bar_even_when_it_is_the_first(
        self,
    ) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)  # accepted at 09:15, exactly when the first bar opens
        rig.broker.submit(limit_order(BUY, "98"))

        assert prices(rig.broker.match(ohlc("100", "101", "97", "99", minutes=0))) == ["98"]

    def test_the_time_a_strategy_writes_on_its_signal_cannot_bring_a_fill_forward(self) -> None:
        """Eligibility comes from the broker's clock. A request has no timestamp at all."""
        assert "ts" not in SimOrderRequest.__dataclass_fields__
        rig = BrokerRig()
        rig.step(ohlc("100", "101", "97", "99", minutes=0))
        rig.broker.submit(limit_order(BUY, "98"))
        assert rig.broker.match(ohlc("100", "101", "97", "99", minutes=0)) == []  # bar t again

    def test_an_order_waits_for_a_later_crossing_bar(self) -> None:
        rig = BrokerRig()
        rig.step(ohlc("100", "101", "99", "100", minutes=0))
        rig.broker.submit(limit_order(BUY, "98"))

        assert rig.step(ohlc("100", "101", "99", "100", minutes=5)) == []
        assert prices(rig.step(ohlc("99", "99", "97", "98", minutes=10))) == ["98"]


class TestLimitFills:
    def test_a_sell_fills_when_the_high_trades_above_its_limit(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(SELL, "102"))
        assert rig.broker.match(ohlc("100", "102", "99", "101")) == []  # touch only
        assert prices(rig.broker.match(ohlc("100", "102.05", "99", "101", minutes=5))) == ["102"]

    def test_touch_crossing_is_available_and_fills_at_the_limit(self) -> None:
        rig = BrokerRig(BarFillModel(crossing=TouchBar()))
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98"))
        assert prices(rig.broker.match(ohlc("100", "102", "98", "100"))) == ["98"]

    def test_the_updates_say_what_happened(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        placed = rig.broker.submit(limit_order(BUY, "98", tag="abc"))
        assert placed.update.status is S.WORKING and placed.update.ordertag == "abc"
        assert placed.fill is None

        (filled,) = rig.step(ohlc("100", "101", "97", "99"))

        assert filled.update.status is S.FILLED
        assert (filled.update.filled_quantity, filled.update.quantity) == (10, 10)
        assert filled.update.average_price == Money.of("98")
        assert filled.fill.reason is FillReason.MATCHED

    def test_bars_of_other_instruments_are_ignored(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98"))
        assert (
            rig.broker.match(ohlc("100", "101", "50", "99", instrument_id=OTHER_INSTRUMENT)) == []
        )
        assert len(rig.broker.open_orders()) == 1

    def test_a_partial_bar_fills_nothing(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98"))
        assert rig.broker.match(ohlc("100", "101", "90", "99", partial=True)) == []


class TestPartialFillsAndSharedLiquidity:
    def test_an_order_larger_than_the_bar_allows_fills_over_several_bars(self) -> None:
        rig = BrokerRig()  # 10% participation
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98", quantity=25))

        first = rig.step(ohlc("99", "99", "97", "98", volume=100))  # room for 10
        second = rig.step(ohlc("99", "99", "97", "98", minutes=5, volume=100))  # 10 more
        third = rig.step(ohlc("99", "99", "97", "98", minutes=10, volume=1000))  # the last 5

        assert [e.update.status for e in first + second + third] == [
            S.PARTIALLY_FILLED, S.PARTIALLY_FILLED, S.FILLED,
        ]  # fmt: skip
        assert [e.fill.quantity for e in first + second + third] == [10, 10, 5]
        assert third[0].update.filled_quantity == 25

    def test_two_orders_share_one_bars_liquidity_in_the_order_they_were_placed(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98", quantity=8, tag="first"))
        rig.broker.submit(limit_order(BUY, "98", quantity=8, tag="second"))

        events = rig.step(ohlc("99", "99", "97", "98", volume=100))  # room for 10 in total

        assert [(e.update.ordertag, e.fill.quantity) for e in events] == [
            ("first", 8),
            ("second", 2),
        ]

    def test_no_volume_no_fill(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98"))
        assert rig.step(ohlc("99", "99", "97", "98", volume=0)) == []

    def test_partial_fill_average_price_is_volume_weighted(self) -> None:
        # gap-aware pricing fills at each bar's open (better than the 99 limit), so prices differ
        model = BarFillModel(
            price=GapAwareSlippage(Decimal(0)), liquidity=BarParticipation(Decimal(1))
        )
        rig = BrokerRig(model)
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "99", quantity=30))

        (first,) = rig.step(ohlc("90", "99", "89", "95", volume=10))  # 10 @ 90
        (second,) = rig.step(ohlc("96", "99", "95", "97", minutes=5, volume=20))  # 20 @ 96

        assert first.update.average_price == Money.of("90")
        assert second.update.status is S.FILLED
        # (10*90 + 20*96) / 30 = 2820 / 30 = 94
        assert second.update.average_price == Money.of("94")


class TestStopLimit:
    def test_the_bar_that_triggers_a_stop_cannot_fill_it(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(stop_order(SELL, trigger="98", limit="97"))

        # this bar reaches the trigger (low 96) AND trades through the 97 limit: still no fill
        assert rig.step(ohlc("99", "99", "96", "97")) == []
        # the next bar trades through the limit: now it fills
        assert prices(rig.step(ohlc("97", "98", "96.5", "97", minutes=5))) == ["97"]

    def test_the_triggering_bar_cannot_fill_the_stop_even_when_it_is_presented_again(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(stop_order(SELL, trigger="98", limit="97"))
        trigger_bar = ohlc("99", "99", "96", "97")

        assert rig.step(trigger_bar) == []
        assert rig.broker.match(trigger_bar) == []  # armed at this bar's close: not before it

    def test_an_untriggered_stop_never_fills_however_low_the_limit_trades(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(stop_order(SELL, trigger="95", limit="94"))
        assert rig.step(ohlc("99", "100", "96", "97")) == []
        assert rig.step(ohlc("97", "98", "95.5", "96", minutes=5)) == []

    def test_a_stop_limit_that_gaps_through_its_limit_waits_for_the_price_to_come_back(
        self,
    ) -> None:
        """The known stop-limit risk: the stop fires, the market falls straight past the limit,
        and the position stays open until price recovers to the limit (or the day ends)."""
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(stop_order(SELL, trigger="98", limit="97"))
        rig.step(ohlc("99", "99", "97.5", "98"))  # triggers
        assert (
            rig.step(ohlc("95", "96", "94", "95", minutes=5)) == []
        )  # a sell at 97 is out of reach
        assert prices(rig.step(ohlc("95", "98", "94", "97", minutes=10))) == ["97"]

    def test_a_buy_stop_triggers_on_the_way_up(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(stop_order(BUY, trigger="102", limit="103", tag="b"))
        assert rig.step(ohlc("101", "102.5", "100", "102")) == []  # triggers
        assert prices(rig.step(ohlc("102", "104", "101", "103", minutes=5))) == ["103"]

    def test_a_stop_placed_during_a_bar_cannot_be_triggered_by_that_bar(self) -> None:
        rig = BrokerRig()
        rig.step(ohlc("99", "99", "96", "97"))  # a bar that WOULD have triggered it
        rig.broker.submit(stop_order(SELL, trigger="98", limit="97"))
        assert rig.broker.match(ohlc("99", "99", "96", "97")) == []  # same bar again: not eligible
        assert (
            rig.step(ohlc("99", "99", "98.5", "99", minutes=5)) == []
        )  # a calm bar: still waiting

    def test_stop_orders_are_validated(self) -> None:
        with pytest.raises(ValueError, match="wrong side"):
            stop_order(SELL, trigger="98", limit="99")
        with pytest.raises(ValueError, match="wrong side"):
            stop_order(BUY, trigger="102", limit="101")
        with pytest.raises(ValueError, match="needs a positive trigger"):
            SimOrderRequest(INSTRUMENT, SELL, OrderType.STOPLOSS_LIMIT, 1, Money.of("1"), "x")
        with pytest.raises(ValueError, match="only stop-loss"):
            SimOrderRequest(INSTRUMENT, SELL, OrderType.LIMIT, 1, Money.of("1"), "x", Money.of("1"))


class TestRejection:
    def test_an_always_rejecting_exchange_refuses_and_never_fills(self) -> None:
        rig = BrokerRig(rejects=SeededRejectRate(10_000, seed=1))
        rig.clock.set(T0)

        event = rig.broker.submit(limit_order(BUY, "98"))

        assert event.update.status is S.REJECTED and event.update.message
        assert rig.broker.open_orders() == ()
        assert rig.step(ohlc("99", "99", "90", "98")) == []

    def test_the_same_seed_rejects_the_same_orders(self) -> None:
        def outcomes(seed: int) -> list[bool]:
            rig = BrokerRig(rejects=SeededRejectRate(3000, seed=seed))
            rig.clock.set(T0)
            return [
                rig.broker.submit(limit_order(BUY, "98", tag=f"t{n}")).update.status is S.REJECTED
                for n in range(200)
            ]

        assert outcomes(7) == outcomes(7)
        assert outcomes(7) != outcomes(8)
        assert 30 < sum(outcomes(7)) < 90  # about 30% of 200

    def test_zero_rate_never_rejects(self) -> None:
        policy = SeededRejectRate(0, seed=3)
        assert all(policy.rejection(limit_order(tag=str(n))) is None for n in range(500))
        assert NeverReject().rejection(limit_order()) is None

    def test_rate_must_be_a_valid_basis_point_figure(self) -> None:
        for rate in (-1, 10_001):
            with pytest.raises(ValueError):
                SeededRejectRate(rate, seed=0)


class TestCancelAndExpire:
    def test_cancel_stops_an_order_filling(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        placed = rig.broker.submit(limit_order(BUY, "98"))

        cancelled = rig.broker.cancel(placed.order_id)

        assert cancelled.update.status is S.CANCELLED
        assert rig.step(ohlc("99", "99", "90", "98")) == []

    def test_cancelling_a_finished_or_unknown_order_is_an_error(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        placed = rig.broker.submit(limit_order(BUY, "98"))
        rig.broker.cancel(placed.order_id)
        with pytest.raises(OrderNotOpenError):
            rig.broker.cancel(placed.order_id)
        with pytest.raises(UnknownOrderError):
            rig.broker.cancel("SIM-999999")
        with pytest.raises(UnknownOrderError):
            rig.broker.order("nope")

    def test_expiring_a_session_cancels_everything_still_resting(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(BUY, "98", tag="a"))
        rig.broker.submit(stop_order(SELL, tag="b"))

        events = rig.broker.expire_open_orders()

        assert [e.update.status for e in events] == [S.CANCELLED, S.CANCELLED]
        assert "expired" in events[0].update.message
        assert rig.broker.open_orders() == ()
        assert rig.broker.expire_open_orders() == []

    def test_a_partly_filled_order_that_expires_keeps_what_it_filled(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        placed = rig.broker.submit(limit_order(BUY, "98", quantity=25))
        rig.step(ohlc("99", "99", "97", "98", volume=100))

        (expired,) = rig.broker.expire_open_orders()

        assert expired.update.filled_quantity == 10 and expired.update.status is S.CANCELLED
        assert rig.broker.order(placed.order_id).filled == 10


class TestIdempotencyAndIds:
    def test_a_reused_tag_is_refused_not_duplicated(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        rig.broker.submit(limit_order(tag="same"))
        with pytest.raises(DuplicateTagError):
            rig.broker.submit(limit_order(tag="same"))
        assert len(rig.broker.open_orders()) == 1

    def test_ids_and_fill_numbers_are_sequential_and_deterministic(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        a = rig.broker.submit(limit_order(BUY, "98", tag="a"))
        b = rig.broker.submit(limit_order(BUY, "98", tag="b"))
        events = rig.step(ohlc("99", "99", "97", "98", volume=1000))

        assert (a.order_id, b.order_id) == ("SIM-000001", "SIM-000002")
        assert [e.fill.sequence for e in events] == [1, 2]


class TestForcedSquareOff:
    def test_it_fills_at_the_given_price_and_says_it_was_forced(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0 + timedelta(hours=6))

        event = rig.broker.square_off(INSTRUMENT, SELL, 10, Money.of("99.5"))

        assert event.fill.reason is FillReason.FORCED_SQUARE_OFF
        assert (event.fill.quantity, event.fill.price) == (10, Money.of("99.5"))
        assert event.update.status is S.FILLED
        assert rig.broker.open_orders() == ()


class TestNoMarketOrders:
    def test_a_market_order_is_unrepresentable(self) -> None:
        for forbidden in ("MARKET", "IOC"):
            with pytest.raises(TypeError):
                SimOrderRequest(INSTRUMENT, BUY, forbidden, 1, Money.of("1"), "x")  # type: ignore[arg-type]
        assert {t.value for t in OrderType} == {"LIMIT", "STOPLOSS_LIMIT"}

    def test_requests_are_validated(self) -> None:
        for kwargs in (
            {"quantity": 0},
            {"tag": ""},
            {"instrument_id": ""},
            {"limit_price": Money.zero()},
        ):
            base = {
                "instrument_id": INSTRUMENT, "side": BUY, "order_type": OrderType.LIMIT,
                "quantity": 1, "limit_price": Money.of("1"), "tag": "t",
            } | kwargs  # fmt: skip
            with pytest.raises(ValueError):
                SimOrderRequest(**base)  # type: ignore[arg-type]


def test_the_broker_needs_only_a_clock() -> None:
    rig = BrokerRig()
    assert isinstance(rig.broker, SimulatedBroker)

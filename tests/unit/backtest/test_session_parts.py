"""EM-106: the parts around the engine — settlement order, square-off planning, settings,
and the as-of universe."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.clock import BarClock
from emporos.backtest.costs import BacktestCosts
from emporos.backtest.fill_model import BarParticipation, GapAwareSlippage, ThroughBar, TouchBar
from emporos.backtest.flow import EventSettler, OrderEventQueue, OrderFlow, RunCounters
from emporos.backtest.orders import FillReason
from emporos.backtest.portfolio import BacktestPortfolio
from emporos.backtest.pricing import MarketableLimitPricing, PassThroughGate
from emporos.backtest.settings import FillModelFactory, FillSettings
from emporos.backtest.square_off import ForcedClosePricing, SessionSquareOff
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.domain.instruments import Exchange, Instrument, UnknownInstrumentError
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from tests.support.backtest import BrokerRig, limit_order, ohlc
from tests.support.backtest_engine import FixedSchedule, FixedTicks
from tests.support.strategies import INSTRUMENT, T0, make_signal

D = Decimal


class RecordingReceiver:
    """The strategy runner's side of the settler, recording the position at each update."""

    def __init__(self, portfolio: BacktestPortfolio) -> None:
        self._portfolio = portfolio
        self.seen: list[tuple[str, int]] = []

    async def handle_order_update(self, update: OrderUpdate) -> None:
        self.seen.append(
            (update.status.value, self._portfolio.position(update.instrument_id).net_quantity)
        )


class NoCutoff:
    def blocks(self, signal: object) -> bool:
        return False


class TestSettlement:
    def build(self):  # type: ignore[no-untyped-def]
        rig = BrokerRig()
        portfolio = BacktestPortfolio(Money.of("100000"))
        queue, counters = OrderEventQueue(), RunCounters()
        receiver = RecordingReceiver(portfolio)
        settler = EventSettler(queue, BacktestCosts(FixedSchedule()), portfolio, receiver, counters)
        return rig, portfolio, queue, counters, receiver, settler

    async def test_a_fill_is_booked_before_the_strategy_hears_about_it(self) -> None:
        rig, portfolio, queue, counters, receiver, settler = self.build()
        rig.clock.set(T0)
        queue.push(rig.broker.submit(limit_order(OrderSide.BUY, "98", quantity=10)))
        for event in rig.step(ohlc("99", "99", "97", "98")):
            queue.push(event)

        await settler.settle()

        assert receiver.seen == [("WORKING", 0), ("FILLED", 10)]  # the position was already 10
        assert counters.fills == 1 and portfolio.fees.amount > 0  # and charges were taken

    async def test_forced_square_offs_are_counted_separately(self) -> None:
        rig, portfolio, queue, counters, receiver, settler = self.build()
        rig.clock.set(T0)
        queue.push(rig.broker.square_off(INSTRUMENT, OrderSide.BUY, 5, Money.of("100")))

        await settler.settle()

        assert (counters.fills, counters.forced_square_offs) == (1, 1)

    async def test_rejections_and_cancellations_are_counted(self) -> None:
        from emporos.backtest.rejects import SeededRejectRate

        rig = BrokerRig(rejects=SeededRejectRate(10_000, 1))
        portfolio = BacktestPortfolio(Money.of("100000"))
        queue, counters = OrderEventQueue(), RunCounters()
        settler = EventSettler(
            queue, BacktestCosts(FixedSchedule()), portfolio, RecordingReceiver(portfolio), counters
        )
        rig.clock.set(T0)
        queue.push(rig.broker.submit(limit_order()))
        await settler.settle()
        assert counters.exchange_rejections == 1

        ok = BrokerRig()
        ok.clock.set(T0)
        placed = ok.broker.submit(limit_order(tag="b"))
        queue.push(ok.broker.cancel(placed.order_id))
        await settler.settle()
        assert counters.cancelled_or_expired == 1

    async def test_the_flow_names_orders_with_a_running_counter(self) -> None:
        rig = BrokerRig()
        queue, counters = OrderEventQueue(), RunCounters()
        flow = OrderFlow(
            rig.broker,
            PassThroughGate(),
            MarketableLimitPricing(D(5), FixedTicks()),
            NoCutoff(),
            queue,
            counters,
        )
        rig.clock.set(T0)

        await flow.submit(make_signal(price="100"))
        await flow.submit(make_signal(price="100"))

        tags = []
        while (event := queue.pop()) is not None:
            tags.append(event.update.ordertag)
        assert tags == ["BT00000001", "BT00000002"] and counters.orders == 2

    async def test_the_cutoff_refuses_the_strategy_but_not_the_sessions_own_exit(self) -> None:
        class BlockAll:
            def blocks(self, signal: object) -> bool:
                return True

        rig = BrokerRig()
        queue, counters = OrderEventQueue(), RunCounters()
        flow = OrderFlow(
            rig.broker, PassThroughGate(), MarketableLimitPricing(D(5), FixedTicks()),
            BlockAll(), queue, counters,
        )  # fmt: skip
        rig.clock.set(T0)

        await flow.submit(make_signal(price="100"))  # the strategy's: refused
        assert (counters.signals, counters.refused_after_square_off, counters.orders) == (1, 1, 0)

        await flow.submit_system(make_signal(price="100"))  # the session's own: goes out
        assert (counters.square_off_signals, counters.orders) == (1, 1)
        assert counters.signals == 1  # a system exit is not the strategy's signal

    def test_an_empty_queue_pops_nothing(self) -> None:
        assert OrderEventQueue().pop() is None


class TestSessionSquareOff:
    def bar(self, closes_at_ist: str, instrument: str = INSTRUMENT, day: int = 0):  # type: ignore[no-untyped-def]
        hour, minute = (int(x) for x in closes_at_ist.split(":"))
        ist = datetime(2026, 1, 5 + day, hour, minute) - timedelta(minutes=5)
        opened = (ist - timedelta(hours=5, minutes=30)).replace(tzinfo=UTC)
        base = ohlc("100", "101", "99", "100", instrument_id=instrument)
        from dataclasses import replace

        return replace(base, ts=opened)

    def policy(self, clock: BarClock | None = None) -> SessionSquareOff:
        return SessionSquareOff(time(15, 15), "run-1", clock or BarClock(T0))

    def test_nothing_before_the_square_off_time(self) -> None:
        plan = self.policy().plan(self.bar("15:10"), Position(INSTRUMENT, 10, Money.of("100")), ())
        assert plan is None

    def test_at_the_square_off_time_a_long_gets_one_sell_for_the_whole_position(self) -> None:
        plan = self.policy().plan(self.bar("15:15"), Position(INSTRUMENT, 10, Money.of("100")), ())

        assert plan is not None and plan.cancel == ()
        exit_signal = plan.exit
        assert (exit_signal.side, exit_signal.quantity, exit_signal.limit_price) == (
            OrderSide.SELL, 10, Money.of("100"),
        )  # fmt: skip
        assert exit_signal.kind.value == "EXIT" and "square-off at 15:15" in exit_signal.reason
        assert exit_signal.strategy_run_id == "run-1"

    def test_a_short_gets_a_buy(self) -> None:
        plan = self.policy().plan(self.bar("15:20"), Position(INSTRUMENT, -7, Money.of("100")), ())
        assert plan is not None and (plan.exit.side, plan.exit.quantity) == (OrderSide.BUY, 7)

    def test_a_flat_instrument_needs_nothing(self) -> None:
        assert self.policy().plan(self.bar("15:15"), Position.flat(INSTRUMENT), ()) is None

    def test_only_once_per_instrument_per_day_but_again_the_next_day(self) -> None:
        policy = self.policy()
        held = Position(INSTRUMENT, 10, Money.of("100"))

        assert policy.plan(self.bar("15:15"), held, ()) is not None
        assert policy.plan(self.bar("15:20"), held, ()) is None
        assert policy.plan(self.bar("15:15", day=1), held, ()) is not None

    def test_each_instrument_has_its_own_plan(self) -> None:
        policy = self.policy()
        assert (
            policy.plan(self.bar("15:15"), Position(INSTRUMENT, 1, Money.of("1")), ()) is not None
        )
        other = "NSE:1002"
        assert (
            policy.plan(self.bar("15:15", other), Position(other, 1, Money.of("1")), ()) is not None
        )

    def test_resting_orders_of_that_instrument_are_cancelled_first(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        mine = rig.broker.submit(limit_order(tag="a"))
        other = rig.broker.submit(limit_order(tag="b", instrument_id="NSE:1002"))
        resting = rig.broker.open_orders()

        plan = self.policy().plan(
            self.bar("15:15"), Position(INSTRUMENT, 10, Money.of("100")), resting
        )

        assert plan is not None and plan.cancel == (mine.order_id,)
        assert other.order_id not in plan.cancel


class TestTheSessionOwnsTheInstrumentAfterSquareOff:
    def test_the_strategy_is_blocked_only_once_the_plan_exists_and_only_that_day_and_instrument(
        self,
    ) -> None:
        clock = BarClock(T0)
        policy = SessionSquareOff(time(15, 15), "run-1", clock)
        signal = make_signal(price="100")
        clock.set(T0 + timedelta(hours=6))  # 15:15 IST on the fixture day
        held = Position(INSTRUMENT, 10, Money.of("100"))
        closing_bar = TestSessionSquareOff().bar("15:15")

        assert policy.blocks(signal) is False  # nothing planned yet
        assert policy.plan(closing_bar, held, ()) is not None
        assert policy.blocks(signal) is True  # now the session owns the position
        assert policy.blocks(make_signal(instrument_id="NSE:1002")) is False  # another instrument
        clock.set(T0 + timedelta(days=1))
        assert policy.blocks(signal) is False  # tomorrow is a new day

    def test_the_day_is_the_clocks_not_the_signals(self) -> None:
        clock = BarClock(T0 + timedelta(hours=6))
        policy = SessionSquareOff(time(15, 15), "run-1", clock)
        policy.plan(TestSessionSquareOff().bar("15:15"), Position(INSTRUMENT, 1, Money.of("1")), ())

        old = make_signal(ts=T0 - timedelta(days=30))  # a strategy cannot dodge it by back-dating
        assert policy.blocks(old) is True


class TestForcedClosePricing:
    def test_a_cover_pays_more_and_a_sell_gets_less_rounded_against_us(self) -> None:
        pricing = ForcedClosePricing(D(10))  # 10 bps
        # 101 * 1.001 = 101.101 -> up to 101.11 ; 101 * 0.999 = 100.899 -> down to 100.89
        assert pricing.price(OrderSide.BUY, Money.of("101")) == Money.of("101.11")
        assert pricing.price(OrderSide.SELL, Money.of("101")) == Money.of("100.89")

    def test_zero_penalty_is_the_last_price(self) -> None:
        assert ForcedClosePricing(D(0)).price(OrderSide.SELL, Money.of("55.55")) == Money.of(
            "55.55"
        )

    def test_a_negative_penalty_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ForcedClosePricing(D(-1))

    def test_the_broker_records_the_reason(self) -> None:
        rig = BrokerRig()
        rig.clock.set(T0)
        event = rig.broker.square_off(INSTRUMENT, OrderSide.SELL, 1, Money.of("1"))
        assert event.fill is not None and event.fill.reason is FillReason.FORCED_SQUARE_OFF
        assert isinstance(rig.broker, SimulatedBroker)


class TestFillSettings:
    def test_the_defaults_are_the_conservative_ones(self) -> None:
        s = FillSettings()
        assert (s.crossing, s.participation, s.slippage_bps) == ("through", D("0.1"), None)
        assert (s.reject_rate_bps, s.forced_close_penalty_bps) == (0, D(10))
        assert "through crossing" in s.describe() and "own limit" in s.describe()

    def test_unknown_keys_and_yaml_floats_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            FillSettings.model_validate({"crossing": "through", "speed": 1})
        with pytest.raises(ValidationError):
            FillSettings.model_validate({"participation": 0.1})  # a float has lost exactness
        assert FillSettings.model_validate({"participation": "0.25"}).participation == D("0.25")

    @pytest.mark.parametrize(
        "bad",
        [{"participation": "0"}, {"participation": "1.5"}, {"slippage_bps": "-1"},
         {"reject_rate_bps": 10_001}, {"forced_close_penalty_bps": "-1"}, {"crossing": "market"}],
    )  # fmt: skip
    def test_out_of_range_values_are_refused(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            FillSettings.model_validate(bad)

    def test_the_factory_builds_what_the_settings_say(self) -> None:
        factory = FillModelFactory()
        bar = ohlc("100", "102", "98", "100", volume=1000)

        default = factory.model(FillSettings())
        touch = factory.model(FillSettings(crossing="touch", participation=D("0.5")))
        slip = factory.model(FillSettings(slippage_bps=D(10)))

        assert default.capacity(bar) == 100
        assert default.price_if_filled(OrderSide.BUY, Money.of("98"), bar) is None
        assert touch.capacity(bar) == 500
        assert touch.price_if_filled(OrderSide.BUY, Money.of("98"), bar) == Money.of("98")
        assert slip.price_if_filled(OrderSide.BUY, Money.of("99"), bar) == Money.of("99")
        assert {ThroughBar, TouchBar, BarParticipation, GapAwareSlippage}  # the vocabulary exists

    def test_the_rejection_seed_is_used(self) -> None:
        factory = FillModelFactory()
        a = factory.rejects(FillSettings(reject_rate_bps=5000, reject_seed=1))
        b = factory.rejects(FillSettings(reject_rate_bps=5000, reject_seed=1))
        orders = [limit_order(tag=str(n)) for n in range(50)]
        assert [a.rejection(o) for o in orders] == [b.rejection(o) for o in orders]


def instrument(token: str, symbol: str) -> Instrument:
    return Instrument(Exchange.NSE, token, symbol, symbol, 1, Money.of("0.05"))


T = datetime(2026, 1, 1, tzinfo=UTC)


def month(n: int) -> datetime:
    return datetime(2026, n, 1, tzinfo=UTC)


class TestAsOfUniverse:
    ERAS = (
        # RELIANCE: renamed in March. The old name is a superseded version, the new one is current.
        InstrumentEra(instrument("2885", "RELIANCE-EQ"), month(1), month(3)),
        InstrumentEra(instrument("2885", "RELIANCE-NEW-EQ"), month(3), None),
        # DEADCO: delisted in April: only a closed era
        InstrumentEra(instrument("999", "DEADCO-EQ"), month(1), month(4)),
        # NEWCO: listed in June
        InstrumentEra(instrument("777", "NEWCO-EQ"), month(6), None),
    )

    def test_a_delisted_name_is_present_before_it_went_and_the_survivor_after(self) -> None:
        book = AsOfInstruments(self.ERAS)

        feb = book.as_of(month(2)).resolver
        may = book.as_of(month(5)).resolver

        assert feb.by_symbol(Exchange.NSE, "DEADCO-EQ").token == "999"
        with pytest.raises(UnknownInstrumentError):
            may.by_symbol(Exchange.NSE, "DEADCO-EQ")  # gone by May

    def test_the_symbol_in_force_on_the_day_is_the_one_that_resolves(self) -> None:
        book = AsOfInstruments(self.ERAS)

        assert book.as_of(month(2)).resolver.by_symbol(Exchange.NSE, "RELIANCE-EQ").token == "2885"
        with pytest.raises(UnknownInstrumentError):
            book.as_of(month(2)).resolver.by_symbol(Exchange.NSE, "RELIANCE-NEW-EQ")
        assert (
            book.as_of(month(4)).resolver.by_symbol(Exchange.NSE, "RELIANCE-NEW-EQ").token == "2885"
        )

    def test_an_era_is_half_open_so_the_boundary_day_belongs_to_the_new_one(self) -> None:
        book = AsOfInstruments(self.ERAS)
        at_boundary = book.as_of(month(3)).resolver
        assert at_boundary.by_id("NSE:2885").tradingsymbol == "RELIANCE-NEW-EQ"

    def test_an_era_ends_exactly_at_valid_to(self) -> None:
        book = AsOfInstruments(self.ERAS)
        assert len(book.as_of(month(4)).resolver) == 1  # DEADCO's era ends at the start of April
        with pytest.raises(UnknownInstrumentError):
            book.as_of(month(4)).resolver.by_symbol(Exchange.NSE, "DEADCO-EQ")

    def test_not_yet_listed_is_absent(self) -> None:
        assert len(AsOfInstruments(self.ERAS).as_of(month(2)).resolver) == 2  # RELIANCE, DEADCO

    def test_before_recorded_history_is_refused_unless_explicitly_assumed(self) -> None:
        book = AsOfInstruments(self.ERAS)
        early = datetime(2025, 6, 1, tzinfo=UTC)

        strict = book.as_of(early)
        assert len(strict.resolver) == 0 and strict.assumed_ids == frozenset()

        assumed = book.as_of(early, assume_earliest_before_history=True)
        assert assumed.assumed_ids == {"NSE:2885", "NSE:999", "NSE:777"}
        assert assumed.resolver.by_symbol(Exchange.NSE, "RELIANCE-EQ").token == "2885"  # the OLDEST
        assert assumed.moment == early

    def test_the_fallback_never_resurrects_a_name_delisted_before_the_date(self) -> None:
        book = AsOfInstruments(self.ERAS)
        assumed = book.as_of(month(5), assume_earliest_before_history=True)

        with pytest.raises(UnknownInstrumentError):
            assumed.resolver.by_symbol(Exchange.NSE, "DEADCO-EQ")  # its history ended in April
        assert assumed.resolver.by_symbol(Exchange.NSE, "RELIANCE-NEW-EQ").token == "2885"
        # The known limit: NEWCO's recorded history begins in June, after this moment. The master
        # cannot say whether it was listed in May, so the fallback assumes it was (and says so).
        assert assumed.assumed_ids == {"NSE:777"}

    def test_an_era_must_end_after_it_starts(self) -> None:
        with pytest.raises(ValueError):
            InstrumentEra(instrument("1", "X-EQ"), month(3), month(3))

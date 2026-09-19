"""EM-65: a strategy runs a full session on the paper broker, driven only through the `Broker`
interface, over a recorded-style tape — with the real fee schedule and a market-data source that
has no order methods at all, so nothing here can put capital at risk.

The tape is synthetic (the market is closed); the same scenario against live NSE ticks is filed
in EM-99."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

from emporos.broker.models import BrokerOrderStatus, MarketDataMode
from emporos.broker.paper.costs import ScheduledCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.fills import ParticipationLiquidity
from emporos.broker.paper.journal import FillRecorded, OrderStateRecorded, SnapshotRecorded
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.fees import IntradayCharges
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from tests.support.fakes import make_tick
from tests.support.paper_journal import RecordingJournal
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_rig import ID, SBIN
from tests.support.paper_strategy import DipScalp, Intent, ListSignals, StrategyDriver

S = BrokerOrderStatus
CASH = Money.of("1000000")


class Tape:
    """A hand-written stretch of market: (price, shares traded since the previous tick)."""

    def __init__(self, clock: FixedClock) -> None:
        self._clock, self._volume, self._n = clock, 5_000_000, 0

    def ticks(self, *moves: tuple[str, int]) -> Iterator[Tick]:
        """Lazily: the clock reaches each tick's moment only when the tick is consumed, so an
        order placed mid-tape is stamped with that moment, as it would be live."""
        for price, shares in moves:
            self._clock.advance(timedelta(seconds=1))
            self._volume += shares
            self._n += 1
            yield make_tick(
                self._clock.now(), price, volume=self._volume, instrument_id=ID, sequence=self._n
            )


async def session(*moves: tuple[str, int]):  # type: ignore[no-untyped-def]
    clock, market, journal = FixedClock(NOW), FakeMarketData([SBIN], []), RecordingJournal()
    schedule = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21))
    broker = PaperBrokerFactory(
        PaperBrokerConfig("PAPER01", CASH),
        ScheduledCosts(IntradayCharges(schedule)),
        liquidity=ParticipationLiquidity(Decimal("0.5")),
    ).build(market, journal, clock, IdGenerator())
    await broker.subscribe_market_data([ID], MarketDataMode.QUOTE)
    strategy = DipScalp(
        ID, entry=Money.of("99.80"), target=Money.of("100.30"),
        stop_trigger=Money.of("99.30"), stop_limit=Money.of("99.25"), quantity=100,
    )  # fmt: skip
    signals = ListSignals()
    driver = StrategyDriver(broker, strategy, signals, IdGenerator(), market.emit)
    await driver.run(Tape(clock).ticks(*moves))
    return broker, journal, signals, strategy


async def test_a_winning_trade_runs_signal_to_fill_to_pnl_with_no_capital_at_risk() -> None:
    broker, journal, signals, strategy = await session(
        ("100.00", 0),  # baseline reading
        ("99.80", 50),  # dips to the entry: the strategy signals and places its buy limit
        ("99.80", 400),  # trades at the limit with plenty of volume: 100 shares fill
        ("100.10", 300),  # the exit pair is resting; neither is reached yet
        ("100.30", 400),  # the target is reached and fills
        ("100.50", 100),  # the stop leg has been cancelled: nothing more happens
    )

    assert [s.kind for s in signals.signals] == [
        Intent.ENTER, Intent.TAKE_PROFIT, Intent.STOP_LOSS, Intent.CANCEL_STOP,
    ]  # fmt: skip
    book = {o.client_tag: o for o in await broker.get_order_book()}
    assert sorted(o.status.value for o in book.values()) == ["CANCELLED", "FILLED", "FILLED"]
    (position,) = await broker.get_positions()
    assert position.net_quantity == 0
    gross = Money.of("50.00")  # 100 shares x (100.30 - 99.80)
    fees = sum(
        (f.fees for f in journal.of(FillRecorded) if isinstance(f, FillRecorded)), Money.zero()
    )
    assert position.realized_pnl == gross - fees and fees > Money.zero()
    funds = await broker.get_funds()
    assert funds.net == CASH + gross - fees  # the whole result is P&L on paper capital
    assert funds.utilised == Money.zero()  # nothing is left committed
    assert strategy.done


async def test_a_losing_trade_stops_out_through_the_trigger_then_the_limit() -> None:
    broker, journal, signals, _ = await session(
        ("100.00", 0),
        ("99.80", 50),
        ("99.80", 400),  # entry fills
        ("99.40", 300),  # above the trigger: nothing
        ("99.30", 300),  # the stop's trigger fires (no fill on the triggering tick)
        ("99.25", 400),  # the stop-loss limit is now live and fills
        ("99.00", 100),
    )

    assert [s.kind for s in signals.signals][-1] is Intent.CANCEL_TARGET
    (position,) = await broker.get_positions()
    assert position.net_quantity == 0 and position.realized_pnl < Money.of(
        "-55.00"
    )  # -55 + charges
    states = [
        (e.order.client_tag, e.order.status)
        for e in journal.of(OrderStateRecorded)
        if isinstance(e, OrderStateRecorded)
    ]
    assert any(s is S.TRIGGER_PENDING for _, s in states) and any(s is S.OPEN for _, s in states)


async def test_a_dip_that_never_trades_through_the_limit_leaves_an_open_order_and_no_position() -> (
    None
):
    broker, _, signals, _ = await session(
        ("100.00", 0),
        ("99.80", 50),  # signal: buy limit at 99.80
        ("99.90", 500),  # the market bounces away: the wrong side of the limit, however much trades
        ("100.20", 500),
    )

    assert [s.kind for s in signals.signals] == [Intent.ENTER]
    (entry,) = await broker.get_order_book()
    assert (entry.status, entry.filled_quantity) == (S.OPEN, 0)
    assert await broker.get_positions() == []
    assert (await broker.get_funds()).net == CASH


async def test_every_step_reached_the_journal_in_order_with_snapshots_after_each_fill() -> None:
    _, journal, _, _ = await session(
        ("100.00", 0), ("99.80", 50), ("99.80", 400), ("100.30", 400), ("100.30", 400),
    )  # fmt: skip

    kinds = [type(e).__name__ for e in journal.entries]
    assert kinds.count("FillRecorded") == 2 and kinds.count("SnapshotRecorded") == 2
    assert [e.seq for e in journal.entries if isinstance(e, FillRecorded)] == [
        2,
        2,
    ]  # per-order seq
    assert isinstance(journal.of(SnapshotRecorded)[-1], SnapshotRecorded)

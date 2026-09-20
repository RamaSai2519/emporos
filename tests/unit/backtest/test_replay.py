"""EM-101: BarReplay — bar order, sessions, and the runner's begin_session()."""

from __future__ import annotations

from datetime import date, timedelta

from emporos.backtest.feed import ClosedBarFeed, FeedWindow
from emporos.core.clock import IST
from emporos.domain.candles import Timeframe
from emporos.strategies.runner import RunnerState
from tests.support.backtest import InMemoryCandles, ReplayRig, trading_days
from tests.support.strategies import (
    INSTRUMENT,
    T0,
    ScriptedStrategy,
    closes_to_bars,
    make_config,
)

DAY = timedelta(days=1)


def feed_for(bars: list, days: int, timeframe: Timeframe = Timeframe.M5) -> ClosedBarFeed:
    return ClosedBarFeed(
        InMemoryCandles(bars), (INSTRUMENT,), timeframe, FeedWindow(T0, T0 + days * DAY)
    )


async def test_the_clock_is_at_the_bars_close_before_the_observer_and_strategy_see_it() -> None:
    bars = closes_to_bars(["100", "101", "102"])
    rig = ReplayRig()
    observer = rig.observer

    await rig.replay.run(feed_for(bars, 1))

    seen = [(ts, clock) for kind, ts, clock in observer.log if kind == "bar"]
    assert seen == [(b.ts, b.closes_at) for b in bars]


async def test_each_bar_reaches_the_strategy_once_in_order() -> None:
    bars = trading_days(2, ["100", "101", "102"])
    rig = ReplayRig()

    report = await rig.replay.run(feed_for(bars, 2))

    assert [e.ts for e in rig.strategy.events] == [b.ts for b in bars]  # type: ignore[union-attr]
    assert report.unclosed_bars_skipped == 0 and not report.halted


async def test_sessions_are_closed_at_each_day_boundary_and_at_the_end() -> None:
    bars = trading_days(3, ["100", "101"])
    rig = ReplayRig()
    observer = rig.observer

    await rig.replay.run(feed_for(bars, 3))

    sessions = [(day, clock) for kind, day, clock in observer.log if kind == "session"]
    ist_days = [(T0 + n * DAY).astimezone(IST).date() for n in range(3)]
    assert [day for day, _ in sessions] == ist_days
    # each session is closed while the clock still stands at that day's last bar
    assert [clock for _, clock in sessions] == [
        (T0 + n * DAY + timedelta(minutes=10)) for n in range(3)
    ]


async def test_the_session_close_comes_before_the_next_days_first_bar() -> None:
    bars = trading_days(2, ["100", "101"])
    rig = ReplayRig()
    observer = rig.observer

    await rig.replay.run(feed_for(bars, 2))

    kinds = [kind for kind, _, _ in observer.log]
    assert kinds == ["bar", "bar", "session", "bar", "bar", "session"]


async def test_the_strategy_gets_on_session_end_every_day_and_keeps_receiving_bars() -> None:
    bars = trading_days(3, ["100", "101"])
    rig = ReplayRig()

    await rig.replay.run(feed_for(bars, 3))

    assert rig.strategy.calls.count("on_session_end") == 3
    assert rig.strategy.calls.count("on_market_data") == 6
    assert rig.strategy.calls[-1] == "on_shutdown"


async def test_the_last_1h_bar_of_a_session_is_delivered_not_skipped() -> None:
    """EM-99 H7: the 15:15 1h bar is nominally an hour long, so it 'closes' at 16:15 IST. In a
    replay the clock is moved to that instant, so the bar is delivered and the next day's bars
    still arrive in order."""
    first_open = T0 + timedelta(hours=6)  # 09:15 IST + 6h = 15:15 IST
    late = closes_to_bars(["100"], INSTRUMENT, first_open, Timeframe.H1)
    nextday = closes_to_bars(["101"], INSTRUMENT, T0 + DAY, Timeframe.H1)
    rig = ReplayRig(timeframe=Timeframe.H1)

    report = await rig.replay.run(feed_for(late + nextday, 2, Timeframe.H1))

    assert report.unclosed_bars_skipped == 0
    assert [e.ts for e in rig.strategy.events] == [late[0].ts, nextday[0].ts]  # type: ignore[union-attr]
    assert late[0].closes_at.astimezone(IST).hour == 16  # the nominal close is after the session


async def test_an_empty_feed_runs_start_and_shutdown_only() -> None:
    rig = ReplayRig()

    report = await rig.replay.run(feed_for([], 1))

    assert rig.strategy.calls == ["initialize", "on_shutdown"]
    assert report.signals_emitted == 0
    assert rig.runner.state is RunnerState.STOPPED


class TestRunnerBeginSession:
    async def test_events_flow_again_after_a_new_session_begins(self) -> None:
        rig = ReplayRig()
        await rig.runner.start()
        await rig.runner.end_session()
        await rig.runner.handle(closes_to_bars(["100"])[0])
        assert rig.strategy.events == []

        await rig.runner.begin_session()
        await rig.runner.handle(closes_to_bars(["100"])[0])

        assert len(rig.strategy.events) == 1

    async def test_a_halted_strategy_stays_halted_across_sessions(self) -> None:
        rig = ReplayRig(strategy=ScriptedStrategy(make_config(), fail_in="on_market_data"))
        await rig.runner.start()
        await rig.runner.handle(closes_to_bars(["100"])[0])
        assert rig.runner.halted

        await rig.runner.end_session()
        await rig.runner.begin_session()

        assert rig.runner.halted
        await rig.runner.handle(closes_to_bars(["100"])[0])
        assert rig.strategy.calls.count("on_market_data") == 1

    async def test_beginning_a_session_on_a_running_or_stopped_runner_changes_nothing(self) -> None:
        rig = ReplayRig()
        await rig.runner.begin_session()
        assert rig.runner.state is RunnerState.NEW
        await rig.runner.start()
        await rig.runner.begin_session()
        assert rig.runner.state is RunnerState.RUNNING
        await rig.runner.shutdown()
        await rig.runner.begin_session()
        assert rig.runner.state is RunnerState.STOPPED


def test_ist_day_of_the_first_fixture_bar_is_the_expected_calendar_date() -> None:
    assert T0.astimezone(IST).date() == date(2026, 1, 5)

"""`BarReplay` — drives one strategy over a feed of closed bars, session by session.

    for each bar:  clock ─▶ observer.before_bar ─▶ strategy sees the bar ─▶ its signals
    each day's end:  observer.session_ending ─▶ strategy.on_session_end ─▶ observer.session_closed

The order inside a bar is the look-ahead guarantee (plan.md §10): the clock moves to the bar's
close FIRST, so anything the observer settles (fills of orders from earlier bars) happens before
the strategy sees the new bar, and the strategy's answer to this bar can only be acted on by
bars that come after it.

The runner is the same `StrategyRunner` live and paper use; this class only supplies the bars.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from emporos.backtest.clock import BarClock
from emporos.backtest.feed import ClosedBarFeed
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.strategies.runner import ReplayClockSync, RunReport, StrategyRunner


class SessionObserver(Protocol):
    """What the rest of a backtest (broker, portfolio) does around the strategy's bars."""

    async def before_bar(self, bar: Candle) -> None:
        """The clock stands at `bar.closes_at`; the strategy has not seen the bar yet."""
        ...

    async def session_ending(self, day: date) -> None:
        """`day` (an IST date) is ending: the strategy can still be told what happens to its
        orders. Runs BEFORE the strategy's `on_session_end`."""
        ...

    async def session_closed(self, day: date) -> None:
        """`day` is over; the strategy has had its `on_session_end` and hears no more today."""
        ...


class NoObserver:
    async def before_bar(self, bar: Candle) -> None:
        return None

    async def session_ending(self, day: date) -> None:
        return None

    async def session_closed(self, day: date) -> None:
        return None


class BarReplay:
    def __init__(
        self,
        runner: StrategyRunner,
        clock: BarClock,
        observer: SessionObserver | None = None,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._clock_sync = ReplayClockSync(clock)
        self._observer: SessionObserver = observer or NoObserver()

    async def run(self, feed: ClosedBarFeed) -> RunReport:
        await self._runner.start()
        day: date | None = None
        async for bar in feed:
            bar_day = bar.ts.astimezone(IST).date()
            if day is not None and bar_day != day:
                await self._close_session(day)
            day = bar_day
            self._clock_sync.sync_to(bar.closes_at)
            await self._observer.before_bar(bar)
            await self._runner.handle(bar)
        if day is not None:
            await self._close_session(day)
        await self._runner.shutdown()
        return self._runner.report()

    async def _close_session(self, day: date) -> None:
        await self._observer.session_ending(day)
        await self._runner.end_session()
        await self._observer.session_closed(day)
        await self._runner.begin_session()

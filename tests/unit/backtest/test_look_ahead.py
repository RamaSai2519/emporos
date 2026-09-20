"""EM-101 / plan.md §22 Phase 10: a look-ahead-biased strategy is structurally impossible to write.

Not "we test that it does not happen": every route a strategy has to the market is tried here by a
deliberately biased strategy, and each one either shows only the past or does not exist.

The routes: (1) `ctx.history` with an enormous limit, (2) moving the clock, (3) reflection over
everything the strategy was given, (4) the delivered event, (5) importing the machinery that
holds the future (lint + import contracts), (6) signals that claim a future time.
"""

from __future__ import annotations

import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
from scripts.check_strategy_purity import PurityScanner

from emporos.backtest.feed import ClosedBarFeed, FeedWindow
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.context import StrategyContext
from emporos.strategies.runner import WallClockSync
from tests.support.backtest import InMemoryCandles, ReplayRig, reachable_candles
from tests.support.strategies import INSTRUMENT, T0, ScriptedStrategy, closes_to_bars, make_signal

DAY = timedelta(days=1)
BARS = closes_to_bars([str(100 + i) for i in range(30)])


def feed() -> ClosedBarFeed:
    return ClosedBarFeed(
        InMemoryCandles(BARS), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY)
    )


class TestHistoryShowsOnlyThePast:
    async def test_asking_for_everything_returns_only_bars_closed_by_now(self) -> None:
        seen: list[tuple[Candle, tuple[Candle, ...]]] = []

        def peek(event: object, ctx: StrategyContext) -> list[Signal]:
            assert isinstance(event, Candle)
            seen.append((event, ctx.history.bars(INSTRUMENT, Timeframe.M5, 10**9)))
            return []

        rig = ReplayRig(on_data=peek)
        await rig.replay.run(feed())

        assert len(seen) == 30
        for index, (event, history) in enumerate(seen):
            assert all(bar.closes_at <= event.closes_at for bar in history)
            assert history[-1] == event  # the newest thing it can see is the bar it was given
            assert len(history) == index + 1  # and not one bar more

    async def test_the_bar_after_the_current_one_is_never_visible_to_the_strategy(self) -> None:
        visible_tomorrow: list[bool] = []

        def peek(event: object, ctx: StrategyContext) -> list[Signal]:
            assert isinstance(event, Candle)
            later = {b.ts for b in BARS if b.ts > event.ts}
            held = {b.ts for b in ctx.history.bars(INSTRUMENT, Timeframe.M5, 10**9)}
            visible_tomorrow.append(bool(later & held))
            return []

        await ReplayRig(on_data=peek).replay.run(feed())

        assert visible_tomorrow == [False] * 30


class TestTheClockCannotBeMoved:
    async def test_a_strategy_that_tries_to_set_the_clock_is_halted_and_time_is_unchanged(
        self,
    ) -> None:
        def cheat(event: object, ctx: StrategyContext) -> list[Signal]:
            ctx.clock.set(T0 + 10 * DAY)  # type: ignore[attr-defined]
            return []

        rig = ReplayRig(on_data=cheat)
        report = await rig.replay.run(feed())

        assert report.halted and "AttributeError" in (report.halt_reason or "")
        assert rig.alerts.alerts[0][0] == "strategy_halted"
        assert rig.clock.now() < T0 + DAY  # the simulation never jumped

    async def test_a_strategy_that_tries_to_advance_the_clock_is_halted(self) -> None:
        def cheat(event: object, ctx: StrategyContext) -> list[Signal]:
            ctx.clock.advance(timedelta(days=9))  # type: ignore[attr-defined]
            return []

        report = await ReplayRig(on_data=cheat).replay.run(feed())

        assert report.halted


class TestReflectionFindsNoFuture:
    async def test_no_bar_reachable_from_the_strategy_or_its_context_is_from_the_future(
        self,
    ) -> None:
        checked: list[int] = []

        def audit(event: object, ctx: StrategyContext) -> list[Signal]:
            assert isinstance(event, Candle)
            now = ctx.clock.now()
            candles = reachable_candles(ctx)
            checked.append(len(candles))
            assert candles and all(bar.closes_at <= now for bar in candles)
            return []

        report = await ReplayRig(on_data=audit).replay.run(feed())

        assert not report.halted, report.halt_reason
        assert checked[-1] == 30

    def test_the_detector_would_see_the_future_if_the_reader_were_reachable(self) -> None:
        """Control: without this the audit above could pass because it looks at nothing."""

        class LeakyHolder:
            def __init__(self) -> None:
                self.reader = InMemoryCandles(BARS)

        found = reachable_candles(LeakyHolder())

        assert len(found) == len(BARS)

    async def test_the_context_holds_no_reader_feed_or_repository(self) -> None:
        rig = ReplayRig()
        fields = {name: type(getattr(rig.context, name)) for name in rig.context.__slots__}  # type: ignore[attr-defined]

        names = {t.__name__ for t in fields.values()}
        assert not names & {"ClosedBarFeed", "InMemoryCandles", "CandleRepository", "WarmupLoader"}
        assert set(fields) == {"run_id", "config", "clock", "logger", "history", "positions", "rng"}


class TestTheDeliveredEventIsThePresent:
    async def test_every_bar_is_delivered_only_after_it_closed(self) -> None:
        arrival: list[tuple[Candle, object]] = []

        def note(event: object, ctx: StrategyContext) -> list[Signal]:
            assert isinstance(event, Candle)
            arrival.append((event, ctx.clock.now()))
            return []

        await ReplayRig(on_data=note).replay.run(feed())

        assert all(bar.closes_at <= now for bar, now in arrival)

    async def test_a_bar_handed_over_early_is_skipped_and_counted_never_delivered(self) -> None:
        # A runner whose clock is never moved (`WallClockSync`), as live: it alone must refuse.
        rig = ReplayRig(clock_sync=WallClockSync())
        await rig.runner.start()
        early = closes_to_bars(["100"])[0]

        await rig.runner.handle(early)

        assert rig.runner.report().unclosed_bars_skipped == 1
        assert rig.strategy.events == []


class TestTheFutureCannotBeImported:
    @pytest.mark.parametrize(
        "source",
        [
            "import gc\n",
            "import inspect\n",
            "import sys\n",
            "import importlib\n",
            "import builtins\n",
            "import os\n",
            "import ctypes\n",
            "import asyncio\n",
            "import threading\n",
            "from gc import get_objects\n",
            "from sys import modules\n",
            "x = __import__('gc')\n",
        ],
    )
    def test_the_purity_lint_rejects_every_reflection_route(
        self, source: str, tmp_path: Path
    ) -> None:
        strategy = tmp_path / "biased.py"
        strategy.write_text(source, encoding="utf-8")

        assert PurityScanner().scan(strategy) != []

    def test_the_purity_lint_rejects_reading_the_wall_clock_to_time_travel(
        self, tmp_path: Path
    ) -> None:
        strategy = tmp_path / "biased.py"
        strategy.write_text(
            textwrap.dedent("""
            from datetime import datetime
            when = datetime.now()
            """),
            encoding="utf-8",
        )

        assert PurityScanner().scan(strategy) != []


class TestSignalsCannotPullTheFutureForward:
    async def test_a_signal_dated_in_the_future_is_recorded_as_given_but_grants_no_earlier_action(
        self,
    ) -> None:
        """A strategy may write any `ts` on its signal; nothing downstream reads it for timing.
        (The broker keys eligibility off the simulation clock: see test_simulated_broker.)"""
        future = make_signal(kind=SignalKind.ENTRY, side=OrderSide.BUY, ts=T0 + 100 * DAY)
        rig = ReplayRig(strategy=ScriptedStrategy(ReplayRig().config, forever=None))
        rig.strategy.queue_on_session_end.append(future)

        await rig.replay.run(feed())

        assert rig.sink.signals == [future]
        assert rig.clock.now() < T0 + DAY

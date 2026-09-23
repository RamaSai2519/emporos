"""EM-150: the run advertises one `BacktestProgress` snapshot per closed bar, and a broken sink
can slow nothing and break nothing (the run's result is byte-identical to a run without one)."""

from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from emporos.backtest.document import BacktestDocument
from emporos.backtest.progress import (
    BacktestProgress,
    BacktestProgressSink,
    NullBacktestProgressSink,
)
from emporos.domain.money import Money
from tests.support.backtest_engine import WORKED_DAY, bars, config, run


class ProbeSink:
    """Human-readable recorder of every report the engine emits."""

    def __init__(self) -> None:
        self.events: list[BacktestProgress] = []

    def report(self, progress: BacktestProgress) -> None:
        self.events.append(progress)

    def close(self) -> None:
        return None


class ExplodingSink:
    """A sink whose display is broken: raises on report, exactly once (the rig must then drop it
    and keep going, and must stop calling it)."""

    def __init__(self) -> None:
        self.calls = 0

    def report(self, progress: BacktestProgress) -> None:
        self.calls += 1
        raise RuntimeError("the display died")

    def close(self) -> None:
        return None


class TestProgressValue:
    def test_snapshots_carry_the_run_shapes(self) -> None:
        progress = BacktestProgress(
            day=date(2026, 1, 5),
            closes_at=dt.datetime(2026, 1, 5, 3, 45),
            bars_seen=1,
            equity=Money.of("100000"),
            gross_exposure=Money.of("0"),
            open_positions=0,
            closed_trades=0,
            signals=0,
            orders=0,
            fills=0,
            cancelled_or_expired=0,
            forced_square_offs=0,
        )
        assert (progress.equity, progress.bars_seen, progress.day) == (
            Money.of("100000"), 1, date(2026, 1, 5),
        )  # fmt: skip

    def test_snapshots_are_immutable(self) -> None:
        progress = BacktestProgress(
            day=date(2026, 1, 5),
            closes_at=dt.datetime(2026, 1, 5, 3, 45),
            bars_seen=0,
            equity=Money.of("100000"),
            gross_exposure=Money.of("0"),
            open_positions=0,
            closed_trades=0,
            signals=0,
            orders=0,
            fills=0,
            cancelled_or_expired=0,
            forced_square_offs=0,
        )
        with pytest.raises(FrozenInstanceError):
            progress.bars_seen = 99  # type: ignore[misc]


class TestNullSink:
    def test_it_is_a_no_op_that_conforms_to_the_protocol(self) -> None:
        sink: BacktestProgressSink = NullBacktestProgressSink()
        sink.close()
        sink.report(
            BacktestProgress(
                day=date(2026, 1, 5), closes_at=dt.datetime(2026, 1, 5, 3, 45),
                bars_seen=1, equity=Money.of("100000"), gross_exposure=Money.of("0"),
                open_positions=0, closed_trades=0, signals=0, orders=0, fills=0,
                cancelled_or_expired=0, forced_square_offs=0,
            )
        )  # fmt: skip


class TestTheWorkedDayEmitsOneSnapshotPerBar:
    """buy_at=2, sell_at=5 as in EM-106. The snapshot is taken after the bar's fills and marks
    settle but before the strategy sees the bar, so the buy and the exit fill one bar after the
    signal that prompted them — and the progress shows it."""

    async def test_one_event_per_closed_bar_in_order(self) -> None:
        probe = ProbeSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), progress=probe)

        assert [event.bars_seen for event in probe.events] == [1, 2, 3, 4, 5, 6, 7]
        assert {event.day for event in probe.events} == {date(2026, 1, 5)}
        assert all(event.day == event.closes_at.astimezone().date() for event in probe.events)

    async def test_the_first_event_is_the_clean_morning(self) -> None:
        probe = ProbeSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), progress=probe)

        first = probe.events[0]
        assert (first.equity, first.gross_exposure) == (Money.of("100000"), Money.of("0"))
        assert (first.open_positions, first.closed_trades) == (0, 0)
        assert (first.signals, first.orders, first.fills) == (0, 0, 0)

    async def test_the_buy_lands_on_bar_two_after_its_own_signal(self) -> None:
        probe = ProbeSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), progress=probe)

        enter = probe.events[1]
        assert (enter.orders, enter.fills, enter.open_positions) == (0, 0, 0)
        filled = probe.events[2]
        assert (filled.orders, filled.fills, filled.open_positions) == (1, 1, 1)
        assert filled.equity == Money.of("100003.03")

    async def test_the_last_event_is_the_worked_close(self) -> None:
        probe = ProbeSink()
        await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), progress=probe)

        last = probe.events[-1]
        assert last.bars_seen == 7
        assert (last.equity, last.gross_exposure) == (Money.of("100015.83"), Money.of("0"))
        assert (last.open_positions, last.closed_trades) == (0, 1)
        assert (last.signals, last.orders, last.fills) == (2, 2, 2)


class TestARaisingSinkIsDroppedNotTheRun:
    async def test_the_run_completes_identically_and_stops_calling_the_sink(self) -> None:
        broken = ExplodingSink()
        broken_result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), progress=broken)
        silent = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5))

        assert broken.calls == 1  # dropped after the first failure, never called again
        assert BacktestDocument().render(broken_result) == BacktestDocument().render(silent)
        assert len(broken_result.trades) == 1

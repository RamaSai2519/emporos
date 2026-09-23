"""EM-150: `ConsoleBacktestProgressSink` — the live console output of `emporos backtest run`.

Drives the sink off an injectable stream and clock so the exact bytes it emits are testable: one
opening line, carriage-return-updated in-place frames while a trading day is open, a permanent
`day ... done` line at each day rollover and on close, and a clean trailing line before the
metrics summary prints.
"""

from __future__ import annotations

import datetime as dt
from datetime import date
from io import StringIO

import pytest

from emporos.backtest.progress import BacktestProgress
from emporos.cli.progress import ConsoleBacktestProgressSink
from emporos.core.clock import IST
from emporos.domain.money import Money

FIRST = date(2026, 1, 5)
LAST = date(2026, 1, 9)


def event(
    day: date,
    bars: int,
    *,
    equity: str = "100000.00",
    opens: int = 0,
    trades: int = 0,
    fills: int = 0,
    signals: int = 0,
    orders: int = 0,
    hour_min: str = "09:15",
) -> BacktestProgress:
    hour, minute = (int(part) for part in hour_min.split(":"))
    closes_at = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).astimezone(
        dt.UTC
    )
    return BacktestProgress(
        day=day,
        closes_at=closes_at,
        bars_seen=bars,
        equity=Money.of(equity),
        gross_exposure=Money.of("0.00"),
        open_positions=opens,
        closed_trades=trades,
        signals=signals,
        orders=orders,
        fills=fills,
        cancelled_or_expired=0,
        forced_square_offs=0,
    )


def day_done(day: str, equity: str) -> str:
    return (
        f"day {day} done  equity {equity}  exposure 0.00  open 0  "
        f"trades 0  fills 0  signals 0  orders 0"
    )


def sink(stream: StringIO, ticks: list[float], refresh: float = 0.5) -> ConsoleBacktestProgressSink:
    return ConsoleBacktestProgressSink(
        FIRST, LAST, stream=stream, now=lambda: ticks[0], refresh_seconds=refresh
    )


class TestOutputShape:
    def test_the_run_opens_with_a_permanent_line_then_in_place_frames(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        s.report(event(FIRST, bars=1))
        s.close()

        text = stream.getvalue()
        assert text.startswith("backtest run: 2026-01-05 .. 2026-01-09\n")
        assert "\r2026-01-05 09:15  bar 1  equity 100000.00  open 0  trades 0  fills 0  0%" in text

    def test_a_fresh_day_rolls_the_past_day_off_on_a_permanent_line(self) -> None:
        stream = StringIO()
        ticks = [0.0]
        s = sink(stream, ticks)
        s.report(event(FIRST, bars=1, equity="100000.00"))
        ticks[0] = 1.0
        s.report(event(FIRST, bars=2, equity="100003.03"))
        ticks[0] = 2.0
        s.report(event(date(2026, 1, 6), bars=3))
        s.close()

        text = stream.getvalue()
        # the rolled-off day is summarised by its LAST event, not the first bar of the new day
        assert "day 2026-01-05 done  equity 100003.03" in text
        assert "2026-01-06 09:15  bar 3" in text
        assert text.rstrip().endswith(day_done("2026-01-06", "100000.00"))

    def test_close_finishes_the_open_day_and_leaves_a_clean_trailing_line(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        s.report(event(FIRST, bars=7))
        s.close()

        text = stream.getvalue()
        assert text.rstrip().endswith(day_done("2026-01-05", "100000.00"))
        assert text.endswith("\n\n")  # blank line, so the metrics summary prints past the day line

    def test_report_after_close_is_ignored(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        s.close()
        s.report(event(FIRST, bars=1))
        s.close()
        assert stream.getvalue() == ""

    def test_a_window_backwards_in_time_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ConsoleBacktestProgressSink(LAST, FIRST)


class TestThrottling:
    def test_identical_frames_are_not_rewritten(self) -> None:
        stream = StringIO()
        ticks = [0.0]
        s = sink(stream, ticks)
        s.report(event(FIRST, bars=1))
        before = stream.getvalue()
        ticks[0] = 10.0
        s.report(event(FIRST, bars=1))
        assert stream.getvalue() == before

    def test_frames_within_the_refresh_window_are_skipped_then_repainted(self) -> None:
        stream = StringIO()
        ticks = [0.0]
        s = sink(stream, ticks, refresh=0.5)

        s.report(event(FIRST, bars=1))
        ticks[0] = 0.2
        s.report(event(FIRST, bars=2))  # too soon: no new bytes
        before = stream.getvalue()
        assert "bar 2" not in before
        assert before.count("\r") == 1  # only the first frame was ever drawn

        ticks[0] = 1.0
        s.report(event(FIRST, bars=3))
        assert "\r2026-01-05 09:15  bar 3" in stream.getvalue()

    def test_the_percent_tracks_the_window_between_first_and_last(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        s.report(event(FIRST, bars=1))
        s.close()
        text = stream.getvalue()
        assert " 0%" in text

        stream2 = StringIO()
        s2 = sink(stream2, [0.0])
        mid = date(2026, 1, 7)  # two days into a four-day span
        s2.report(event(mid, bars=1))
        s2.close()
        assert " 50%" in stream2.getvalue()

        stream3 = StringIO()
        s3 = sink(stream3, [0.0])
        s3.report(event(LAST, bars=1))
        s3.close()
        assert " 100%" in stream3.getvalue()

    def test_money_is_rendered_to_paisa_with_round_half_even(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        s.report(event(FIRST, bars=1, equity="100015.835"))
        s.close()
        assert "100015.84" in stream.getvalue()

    def test_a_day_before_the_window_is_floored_to_zero_percent(self) -> None:
        stream = StringIO()
        s = sink(stream, [0.0])
        early = date(2025, 12, 29)  # before the first day of the window: defensive floor
        s.report(event(early, bars=1))
        s.close()
        assert " 0%" in stream.getvalue()
        assert stream.getvalue().rstrip().endswith(day_done("2025-12-29", "100000.00"))

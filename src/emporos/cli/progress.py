"""Live console progress for `emporos backtest run`.

A `ConsoleBacktestProgressSink` turns `BacktestProgress` events into something a person can watch:
an opening line, then one refreshed, carriage-return-updated status line per trading day, and a
permanent `day ... done` line when each day rolls over. The final metrics summary is printed on a
fresh line afterwards, unchanged. Everything here is display: it never affects the run's result,
buffers nothing, and `close()` is called on both success and failure so an interrupted run leaves
a clean last line behind.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from io import TextIOBase

from emporos.backtest.progress import BacktestProgress
from emporos.core.clock import IST
from emporos.domain.money import Money

_PAISA = Decimal("0.01")
_REFRESH_SECONDS = 0.5


class ConsoleBacktestProgressSink:
    """`BacktestProgressSink` that shows the run moving on a text stream.

    `stream` and `now` are injectable so tests can read the exact bytes and drive the throttling;
    production uses `sys.stdout` and a monotonic wall clock.
    """

    def __init__(
        self,
        first_day: date,
        last_day: date,
        stream: TextIOBase | None = None,
        now: Callable[[], float] | None = None,
        refresh_seconds: float = _REFRESH_SECONDS,
    ) -> None:
        if first_day > last_day:
            raise ValueError("a progress window cannot start after it ends")
        self._first = first_day
        self._last = last_day
        self._stream = stream or sys.stdout
        self._now = now or time.monotonic
        self._refresh_seconds = refresh_seconds
        self._day_last: BacktestProgress | None = None  # last event of the current `_last_day`
        self._last_day: date | None = None
        self._last_frame: str | None = None
        self._last_write: float | None = None
        self._frame_width = 0
        self._shown = False
        self._closed = False

    def report(self, progress: BacktestProgress) -> None:
        if self._closed:
            return
        if not self._shown:
            self._shown = True
            self._separate(f"backtest run: {self._first.isoformat()} .. {self._last.isoformat()}")
        if self._last_day is not None and progress.day != self._last_day:
            self._separate(self._day_done(self._last_day, self._day_last))
        self._last_day = progress.day
        self._day_last = progress
        self._frame(self._frame_for(progress))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._last_day is not None:
            self._separate(self._day_done(self._last_day, self._day_last))
        if self._shown:
            self._write("\n")
        self._last_frame = None

    def _frame_for(self, progress: BacktestProgress) -> str:
        ist = progress.closes_at.astimezone(IST)
        return (
            f"{progress.day.isoformat()} {ist:%H:%M}  bar {progress.bars_seen}  "
            f"equity {self._money(progress.equity)}  open {progress.open_positions}  "
            f"trades {progress.closed_trades}  fills {progress.fills}  "
            f"{self._percent(progress.day)}%"
        )

    def _day_done(self, day: date, event: BacktestProgress | None) -> str:
        assert event is not None  # a day's rollover/close always follows one of its own events
        return (
            f"day {day.isoformat()} done  equity {self._money(event.equity)}  "
            f"exposure {self._money(event.gross_exposure)}  open {event.open_positions}  "
            f"trades {event.closed_trades}  fills {event.fills}  "
            f"signals {event.signals}  orders {event.orders}"
        )

    def _percent(self, day: date) -> int:
        span = (self._last - self._first).days
        if span <= 0:
            return 100 if day >= self._first else 0
        done = (day - self._first).days
        return min(max(done * 100 // span, 0), 100)

    @staticmethod
    def _money(value: Money) -> str:
        return format(value.amount.quantize(_PAISA, rounding=ROUND_HALF_EVEN), "f")

    def _frame(self, text: str) -> None:
        if text == self._last_frame:
            return
        if self._last_write is not None and self._now() - self._last_write < self._refresh_seconds:
            return
        self._write("\r" + text)
        self._last_frame = text
        self._last_write = self._now()
        self._frame_width = len(text)

    def _separate(self, text: str) -> None:
        prefix = "\r" if self._frame_width else ""
        padding = " " * max(0, self._frame_width - len(text))
        self._write(f"{prefix}{text}{padding}\n")
        self._frame_width = 0

    def _write(self, text: str) -> None:
        self._stream.write(text)
        self._stream.flush()
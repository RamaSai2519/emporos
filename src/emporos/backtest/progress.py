"""Live progress of one backtest run — what a console shows while it runs (plan.md §10).

The run's result, its documents and its metrics know nothing about this module: the report is
a frozen snapshot taken at each closed bar, handed to an injected sink, and never stored. A run
that does not name a sink gets `NullBacktestProgressSink` and behaves exactly as before.

This is the intra-run counterpart of `emporos.backtest.batch.RunProgress` (one finished run of a
batch); the two are deliberately different and have no import relationship.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from emporos.domain.money import Money


@dataclass(frozen=True)
class BacktestProgress:
    """What the run looks like at one closed bar. Every number is a snapshot, taken after the
    bar's fills settled and its prices marked but before the strategy sees the bar, so the next
    bar cannot move a value this bar reported."""

    day: date  # the IST trading day the bar belongs to
    closes_at: datetime  # the bar's close, UTC
    bars_seen: int  # bars replayed so far in this window (warm-up bars are not counted)
    equity: Money
    gross_exposure: Money
    open_positions: int
    closed_trades: int
    signals: int
    orders: int
    fills: int
    cancelled_or_expired: int
    forced_square_offs: int


class BacktestProgressSink(Protocol):
    """Where a run's per-bar progress goes. Implementations must never raise into the run: a
    broken display is dropped, a backtest is not."""

    def report(self, progress: BacktestProgress) -> None: ...

    def close(self) -> None:
        """The run is over; a sink with a live line on screen should finish it here."""
        ...


class NullBacktestProgressSink:
    """No sink: the deterministic path, which costs nothing and changes nothing."""

    def report(self, progress: BacktestProgress) -> None:
        return None

    def close(self) -> None:
        return None

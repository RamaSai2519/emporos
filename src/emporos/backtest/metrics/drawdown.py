"""Drawdowns on the bar-level equity curve, so an intraday plunge that recovers by the close is
still seen. The account starts at `starting_cash`, which is the first peak.

A drawdown starts when equity drops below its running peak and ends when equity gets back to the
peak (equal counts). One still open at the end of the run has no `recovered_at`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.portfolio import EquityPoint

TOP_PERIODS = 5


@dataclass(frozen=True)
class DrawdownPeriod:
    peak_at: datetime
    trough_at: datetime
    recovered_at: datetime | None
    peak_equity: Decimal
    trough_equity: Decimal

    @property
    def depth(self) -> Decimal:
        return DecimalMath.divide(self.peak_equity - self.trough_equity, self.peak_equity)


@dataclass(frozen=True)
class DrawdownReport:
    max_drawdown: Decimal  # a fraction of the peak: 0.25 = down a quarter
    periods: tuple[DrawdownPeriod, ...]  # the deepest few, deepest first
    longest_days: int  # peak to recovery (or to the end of the run), in calendar days
    count: int


@dataclass
class _Open:
    peak_at: datetime
    peak_equity: Decimal
    trough_at: datetime
    trough_equity: Decimal


class DrawdownAnalyzer:
    def analyze(self, starting_cash: Decimal, curve: Sequence[EquityPoint]) -> DrawdownReport:
        if not curve:
            return DrawdownReport(ZERO, (), 0, 0)
        peak_equity, peak_at = starting_cash, curve[0].ts
        current: _Open | None = None
        periods: list[DrawdownPeriod] = []
        for point in curve:
            equity = point.equity.amount
            if equity >= peak_equity:
                if current is not None:
                    periods.append(self._close(current, point.ts))
                    current = None
                peak_equity, peak_at = equity, point.ts
            elif current is None:
                current = _Open(peak_at, peak_equity, point.ts, equity)
            elif equity < current.trough_equity:
                current.trough_at, current.trough_equity = point.ts, equity
        if current is not None:
            periods.append(self._close(current, None))
        return self._report(periods, curve[-1].ts)

    @staticmethod
    def _close(state: _Open, recovered_at: datetime | None) -> DrawdownPeriod:
        return DrawdownPeriod(
            state.peak_at, state.trough_at, recovered_at, state.peak_equity, state.trough_equity
        )

    @staticmethod
    def _report(periods: list[DrawdownPeriod], last: datetime) -> DrawdownReport:
        deepest = sorted(periods, key=lambda p: (-p.depth, p.peak_at))
        longest = (
            max(((p.recovered_at or last) - p.peak_at).days for p in periods) if periods else 0
        )
        return DrawdownReport(
            max_drawdown=deepest[0].depth if deepest else ZERO,
            periods=tuple(deepest[:TOP_PERIODS]),
            longest_days=longest,
            count=len(periods),
        )

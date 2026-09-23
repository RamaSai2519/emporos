"""Performance broken down by regime AND by walk-forward window (EM-184), in one structure: a
`WindowPerformance` per window, each carrying that window's own `by_regime` slice — read straight
from `MetricsReport.by_regime`, already computed by the backtest engine for every out-of-sample
test window, never recomputed here. A pooled, all-windows-merged regime breakdown is deliberately
NOT built: `TradeStatistics`' derived figures (win rate, profit factor) are not meaningfully
additive across windows, and a reviewer comparing regimes window-by-window sees exactly where a
regime's evidence came from, which a single merged number would hide.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.backtest.metrics.trades import TradeStatistics
from emporos.backtest.walkforward_run import WindowOutcome


@dataclass(frozen=True)
class RegimeSlice:
    count: int
    net_pnl: Decimal
    win_rate: Decimal | None

    @classmethod
    def of(cls, stats: TradeStatistics) -> RegimeSlice:
        return cls(stats.count, stats.net_pnl.amount, stats.win_rate)


@dataclass(frozen=True)
class WindowPerformance:
    index: int
    test_start: datetime
    test_end: datetime
    net_pnl: Decimal
    by_regime: dict[str, RegimeSlice]

    @classmethod
    def of(cls, outcome: WindowOutcome) -> WindowPerformance:
        window = outcome.window
        return cls(
            index=window.index,
            test_start=window.test.start,
            test_end=window.test.end,
            net_pnl=outcome.test.metrics.trades.net_pnl.amount,
            by_regime={
                regime: RegimeSlice.of(stats)
                for regime, stats in outcome.test.metrics.by_regime.items()
                if stats.count > 0
            },
        )


def window_performance(outcomes: Sequence[WindowOutcome]) -> tuple[WindowPerformance, ...]:
    return tuple(WindowPerformance.of(outcome) for outcome in outcomes)

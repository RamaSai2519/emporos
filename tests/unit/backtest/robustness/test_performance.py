"""EM-184: `window_performance` — one `WindowPerformance` per walk-forward window, each carrying
that window's own `by_regime` breakdown, straight off the real engine's walk-forward output."""

from __future__ import annotations

from emporos.backtest.robustness.performance import window_performance
from emporos.backtest.tuning import NET_PNL
from tests.unit.backtest.test_walkforward_run import (
    CANDIDATES,
    RecordingBacktester,
    base_spec,
    runner,
    windows,
)


async def test_one_window_performance_entry_per_walk_forward_window() -> None:
    result = await runner(RecordingBacktester(), objective=NET_PNL).run(
        base_spec(), CANDIDATES, windows()
    )

    performance = window_performance(result.outcomes)

    assert len(performance) == len(result.outcomes)
    assert [p.index for p in performance] == [o.window.index for o in result.outcomes]


async def test_each_windows_net_pnl_matches_its_own_metrics() -> None:
    result = await runner(RecordingBacktester(), objective=NET_PNL).run(
        base_spec(), CANDIDATES, windows()
    )

    performance = window_performance(result.outcomes)

    for entry, outcome in zip(performance, result.outcomes, strict=True):
        assert entry.net_pnl == outcome.test.metrics.trades.net_pnl.amount
        assert entry.test_start == outcome.window.test.start
        assert entry.test_end == outcome.window.test.end


async def test_by_regime_only_includes_regimes_actually_traded() -> None:
    result = await runner(RecordingBacktester(), objective=NET_PNL).run(
        base_spec(), CANDIDATES, windows()
    )

    performance = window_performance(result.outcomes)

    for entry, outcome in zip(performance, result.outcomes, strict=True):
        expected_regimes = {
            regime for regime, stats in outcome.test.metrics.by_regime.items() if stats.count > 0
        }
        assert set(entry.by_regime) == expected_regimes
        for regime, stats in outcome.test.metrics.by_regime.items():
            if stats.count > 0:
                assert entry.by_regime[regime].count == stats.count
                assert entry.by_regime[regime].net_pnl == stats.net_pnl.amount

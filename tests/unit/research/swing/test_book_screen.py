"""EM-233: the combined-book screen: sleeves simulated on their share, combined, judged."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import ZERO_SCHEDULE, dataset, hold, series, sessions

from emporos.research.swing.benchmark import EqualWeightBenchmark
from emporos.research.swing.book_cell import SleeveBookCell
from emporos.research.swing.book_screen import BookScreenRun, SleeveWorld
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.simulator import SwingRun

D = Decimal
DAYS = sessions(70, date(2026, 1, 5))
X, ETF = "NSE:1", "NSE:11"


def rising(daily: str) -> list[tuple[str, str]]:
    price, out = D(100), []
    for _ in DAYS:
        nxt = price * (1 + D(daily) / 100)
        out.append((str(price), str(nxt)))
        price = nxt
    return out


def sleeve(name: str, instrument: str, daily: str, exempt: frozenset[str] = frozenset()):  # type: ignore[no-untyped-def]
    data = dataset(series(instrument, DAYS, rising(daily)))

    def benchmark(costs: SwingCostModel) -> SwingRun:
        return EqualWeightBenchmark(data, costs, start_day=DAYS[1]).run()

    return SleeveWorld(
        name, data, lambda: hold(instrument, DAYS[0], DAYS[-2]), 1, D(0), benchmark, exempt
    )


def screen(**kw: object) -> BookScreenRun:
    return BookScreenRun(
        [sleeve("equity", X, "0.3"), sleeve("defensive", ETF, "0.1", frozenset({ETF}))],
        ZERO_SCHEDULE,
        D(10000),
        DAYS[1],
        BlockBootstrap(paths=10),
        **kw,  # type: ignore[arg-type]
    )


class TestBookScreenRun:
    def test_each_sleeve_runs_on_its_share_and_the_book_is_their_sum_at_the_split(self) -> None:
        arm = screen().run((D("0.6"), D("0.4")))

        equity, defensive = (s.run for s in arm.sleeve_runs)

        assert equity.capital == D(6000) and defensive.capital == D(4000)
        assert arm.outcome.arm.capital == D(10000)
        assert arm.label == "60/40"
        assert arm.outcome.arm.days[0] == DAYS[1]

    def test_the_book_is_between_its_sleeves_and_beats_the_slower_one(self) -> None:
        arm = screen().run((D("0.5"), D("0.5")))

        book = arm.outcome.stats.total_return
        fast, slow = (s.run.equity[-1] / s.run.capital - 1 for s in arm.sleeve_runs)

        assert float(slow) < book < float(fast)

    def test_an_exempt_instrument_never_counts_as_the_largest_but_stays_in_the_total(self) -> None:
        arm = screen().run((D("0.5"), D("0.5")))

        by_name = arm.outcome.arm.pnl_by_instrument
        total = sum(by_name.values(), D(0))

        assert arm.exempt == frozenset({ETF})
        assert arm.outcome.stats.max_instrument_share == pytest.approx(float(by_name[X] / total))

    def test_the_benchmark_is_the_blend_of_the_sleeves_own_at_the_same_split(self) -> None:
        arm = screen().run((D("0.5"), D("0.5")))

        assert arm.benchmark.days == arm.outcome.arm.days
        assert arm.outcome.universe_stats.net_cagr > 0

    def test_the_adverse_run_costs_more_than_the_benchmark_run(self) -> None:
        from tests.unit.research.swing.support import ZERO_SCHEDULE as FREE_SCHEDULE

        from emporos.research.swing.costs import BENCHMARK

        run = BookScreenRun(
            [sleeve("equity", X, "0.3"), sleeve("defensive", ETF, "0.1")],
            FREE_SCHEDULE, D(10000), DAYS[1], BlockBootstrap(paths=10),
        )  # fmt: skip
        arm = run.run((D("0.5"), D("0.5")))

        assert arm.outcome.adverse_stats.total_return < arm.outcome.stats.total_return
        assert BENCHMARK.slippage_bps == 10

    def test_the_correlation_is_over_the_two_sleeves_months(self) -> None:
        arm = screen().run((D("0.5"), D("0.5")))

        assert arm.correlation.months >= 2

    def test_a_book_needs_two_sleeves(self) -> None:
        with pytest.raises(ValueError, match="at least two sleeves"):
            BookScreenRun([sleeve("a", X, "0.1")], ZERO_SCHEDULE, D(10000), DAYS[1])


class TestSleeveBookCell:
    def test_a_split_names_the_equity_share_first(self) -> None:
        assert SleeveBookCell.weights({"split": "60/40"}) == (D("0.6"), D("0.4"))
        assert SleeveBookCell.weights({"split": "50/50"}) == (D("0.5"), D("0.5"))

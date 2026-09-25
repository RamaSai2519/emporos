"""The §12.5 numbers, on hand-built runs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from emporos.eventtrader.replay.engine import RunResult, RunStats
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.metrics import (
    LossBootstrap,
    ScenarioMetrics,
    Section125Bars,
    metrics_for,
)
from emporos.eventtrader.replay.records import Scenario, TradeRecord
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side
from tests.unit.eventtrader.replay.fakes import at

D = Decimal
START = date(2024, 3, 4)


def trade(day: date, symbol: str, net: int, cost: int = 10) -> TradeRecord:
    return TradeRecord(
        f"{symbol}{day}", f"NSE:{symbol}", symbol, Instrument.CASH_INTRADAY, Product.INTRADAY,
        Side.LONG, 10, at(day, 10), D(100), at(day, 15), D(100), ExitReason.SQUARE_OFF, D(97),
        D(30), D(net + cost), D(cost), D(cost * 2),
    )  # fmt: skip


def run(trades: list[TradeRecord], token_by_day: dict[date, int] | None = None) -> RunResult:
    stats = RunStats()
    for day, cost in (token_by_day or {}).items():
        stats.token_cost_by_day[day] = D(cost)
    return RunResult(tuple(trades), stats, sum((D(c) for c in (token_by_day or {}).values()), D(0)))


def sessions(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def test_net_is_trading_net_less_the_token_cost_and_the_daily_series_carries_both() -> None:
    d0, d1 = START, START + timedelta(days=1)
    result = run([trade(d0, "AAA", 200), trade(d1, "BBB", -50)], {d0: 30, d1: 5})

    m = metrics_for(result, Scenario.BENCHMARK, sessions(3))

    assert (m.trades, m.net_trading, m.token_cost, m.net_total) == (2, D(150), D(35), D(115))
    assert dict(m.daily)[d0] == D(170) and dict(m.daily)[d1] == D(-55)
    assert dict(m.daily)[START + timedelta(days=2)] == D(0)  # a session with nothing is zero
    assert m.expectancy == D("57.5") and m.wins == 1 and m.win_rate == 0.5


def test_adverse_costs_are_charged_when_asked() -> None:
    result = run([trade(START, "AAA", 200, cost=10)])

    adverse = metrics_for(result, Scenario.ADVERSE, sessions(1))

    assert adverse.net_trading == D(190)  # gross 210 less the adverse cost, Rs 20


def test_max_drawdown_is_peak_to_trough_of_the_running_total() -> None:
    days = sessions(5)
    result = run([trade(days[0], "A", 100), trade(days[1], "B", -300), trade(days[2], "C", -100),
                  trade(days[3], "D", 50)])  # fmt: skip

    m = metrics_for(result, Scenario.BENCHMARK, days)

    assert m.max_drawdown == D(400)


def test_t_statistic_of_the_daily_net() -> None:
    days = sessions(4)
    result = run([trade(d, "A", v) for d, v in zip(days, (100, 100, 200, 200), strict=True)])

    m = metrics_for(result, Scenario.BENCHMARK, days)

    # mean 150, sample sd 57.74, n 4: t = 150 / (57.74 / 2) = 5.196
    assert m.daily_t is not None and abs(m.daily_t - 5.196) < 0.01


def test_t_is_undefined_for_one_day_or_no_variation() -> None:
    assert metrics_for(run([]), Scenario.BENCHMARK, sessions(1)).daily_t is None
    flat = metrics_for(run([]), Scenario.BENCHMARK, sessions(5))
    assert flat.daily_t is None and flat.expectancy is None and flat.win_rate is None


def test_months_are_counted_by_the_day_each_trade_closed_less_that_months_token_cost() -> None:
    result = run(
        [trade(date(2024, 3, 4), "A", 100), trade(date(2024, 4, 2), "B", 100),
         trade(date(2024, 5, 2), "C", -10)],
        {date(2024, 4, 2): 150},
    )  # fmt: skip

    m = metrics_for(result, Scenario.BENCHMARK, [date(2024, 3, 4)])

    assert (m.months_with_trades, m.months_positive) == (3, 1)  # April's 100 is eaten by 150
    assert m.months_positive_share == 1 / 3


def test_the_top_names_share_of_net_profit() -> None:
    d = sessions(3)
    result = run([trade(d[0], "AAA", 300), trade(d[1], "BBB", 100), trade(d[2], "CCC", -100)])

    m = metrics_for(result, Scenario.BENCHMARK, d)

    assert m.top_name == "AAA" and m.top_name_share == 300 / 300  # net profit is 300 in all


def test_no_share_is_reported_when_the_run_made_no_profit() -> None:
    m = metrics_for(run([trade(START, "A", -100)]), Scenario.BENCHMARK, sessions(1))

    assert m.top_name_share is None


class TestBootstrap:
    def test_a_book_that_can_never_lose_enough_has_p_zero_and_one_that_always_does_has_p_one(
        self,
    ) -> None:
        small = [D(1)] * 40
        ruinous = [D(-500)] * 40

        assert LossBootstrap(paths=200).p_loss(small) == 0.0
        assert LossBootstrap(paths=200).p_loss(ruinous) == 1.0

    def test_seeded_so_the_same_returns_give_the_same_answer(self) -> None:
        mixed = [D(v) for v in (300, -200, 150, -400, 50) * 10]

        assert LossBootstrap(paths=300).p_loss(mixed) == LossBootstrap(paths=300).p_loss(mixed)

    def test_a_series_shorter_than_one_block_cannot_be_bootstrapped(self) -> None:
        assert LossBootstrap().p_loss([D(1)] * 5) is None


class TestBars:
    def metrics(self, trades: int, net_each: int, scenario: Scenario) -> ScenarioMetrics:
        days = sessions(trades)
        names = [f"N{i % 10}" for i in range(trades)]
        result = run(
            [
                trade(d, n, net_each + (i % 3) * 5)
                for i, (d, n) in enumerate(zip(days, names, strict=True))
            ]
        )
        return metrics_for(result, scenario, days)

    def test_a_run_that_clears_every_bar_passes_them_all(self) -> None:
        bench = self.metrics(80, 100, Scenario.BENCHMARK)
        adverse = self.metrics(80, 100, Scenario.ADVERSE)

        bars = Section125Bars().evaluate(bench, adverse, 0.02, 0.01)

        assert all(b.passed for b in bars), [b for b in bars if not b.passed]
        assert len(bars) == 9

    def test_every_bar_is_reported_even_after_one_fails(self) -> None:
        bench = self.metrics(20, 100, Scenario.BENCHMARK)
        adverse = self.metrics(20, -100, Scenario.ADVERSE)

        bars = {b.name: b for b in Section125Bars().evaluate(bench, adverse, None, None)}

        assert not bars["trades"].passed and not bars["net P&L at adverse costs > 0"].passed
        assert bars["max drawdown"].passed
        assert bars["P(losing Rs 25,000 in 12 months)"].value == "n/a"
        assert not bars["coin-flip control beaten"].passed

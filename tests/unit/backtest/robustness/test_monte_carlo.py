"""Monte Carlo over trade sequences: exact when hand-worked, deterministic, honest when thin."""

from decimal import Decimal

import pytest

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.backtest.robustness.monte_carlo import (
    MonteCarlo,
    MonteCarloConfig,
    PathStatistics,
    Percentiles,
    Permutation,
    TradeOutcome,
)
from tests.support.backtest_metrics import trade

D = Decimal


class ScriptedSource:
    """A RandomSource that replays a fixed script (modulo the bound), so draws are known."""

    def __init__(self, *script: int) -> None:
        self._script = script
        self._at = 0

    def below(self, bound: int) -> int:
        value = self._script[self._at % len(self._script)] % bound
        self._at += 1
        return value


def ledger(*nets: str) -> list[ClosedTrade]:
    return [trade(net, minute * 2) for minute, net in enumerate(nets)]


def config(**overrides: object) -> MonteCarloConfig:
    values: dict[str, object] = {
        "seed": 7, "resamples": 200, "starting_equity": D(1000), "min_trades": 2,
    }  # fmt: skip
    values.update(overrides)
    return MonteCarloConfig(**values)  # type: ignore[arg-type]


def outcomes(*nets: str) -> list[TradeOutcome]:
    return [TradeOutcome.of(t) for t in ledger(*nets)]


class TestPathStatistics:
    def test_max_drawdown_is_the_deepest_fall_from_the_running_peak(self) -> None:
        # equity: 1000 -> 1100 -> 800 -> 850; the deepest fall is 300 from a peak of 1100
        stats = PathStatistics(D(1000))

        assert stats.max_drawdown(outcomes("100", "-300", "50")) == DecimalMath.divide(
            D(300), D(1100)
        )

    def test_a_losing_start_counts_from_the_starting_equity(self) -> None:
        stats = PathStatistics(D(1000))

        assert stats.max_drawdown(outcomes("-100", "-100")) == D("0.2")

    def test_no_drawdown_when_equity_only_rises(self) -> None:
        assert PathStatistics(D(1000)).max_drawdown(outcomes("10", "20")) == D(0)

    def test_profit_factor_is_profit_over_loss_and_undefined_without_a_loss(self) -> None:
        assert PathStatistics.profit_factor(outcomes("300", "-100", "-100")) == DecimalMath.divide(
            D(300), D(200)
        )
        assert PathStatistics.profit_factor(outcomes("300", "50")) is None

    def test_expectancy_is_the_mean_return_on_notional(self) -> None:
        # notional is 1,000 per trade: returns 10% and -5%
        assert PathStatistics.expectancy(outcomes("100", "-50")) == D("0.025")


class TestTradeOutcome:
    def test_refuses_a_trade_that_entered_no_notional(self) -> None:
        with pytest.raises(ValueError, match="positive notional"):
            TradeOutcome.of(trade("5", 0, price="0"))


class TestPercentiles:
    def test_nearest_rank_returns_a_value_that_occurred(self) -> None:
        sample = [D(n) for n in range(1, 11)]

        assert Percentiles.of(sample, D("0.5")) == D(5)
        assert Percentiles.of(sample, D("0.95")) == D(10)
        assert Percentiles.of(sample, D("0.025")) == D(1)

    def test_interval_carries_the_observed_value_and_the_tails(self) -> None:
        interval = Percentiles.interval(D(5), [D(n) for n in range(1, 101)], D("0.9"))

        assert (interval.observed, interval.low, interval.high) == (D(5), D(5), D(95))


class TestPermutation:
    def test_keeps_every_trade_and_reorders(self) -> None:
        path = outcomes("1", "2", "3")

        drawn = Permutation().draw(path, ScriptedSource(0))

        # a source that always answers 0 swaps each position with the first: [1,2,3] -> [2,3,1]
        assert [o.net for o in drawn] == [D(2), D(3), D(1)]
        assert sorted(o.net for o in drawn) == [D(1), D(2), D(3)]


class TestDistribution:
    def test_permuting_cannot_change_net_pnl_so_only_the_path_moves(self) -> None:
        report = MonteCarlo(config()).run(ledger("100", "-300", "50", "-20", "80"))

        assert report.observed.net_pnl == D(-90)
        assert report.observed.max_drawdown == DecimalMath.divide(D(300), D(1100))
        assert report.distribution is not None
        low, high = report.distribution.max_drawdown.low, report.distribution.max_drawdown.high
        assert low <= report.distribution.max_drawdown.observed <= high  # type: ignore[operator]

    def test_bootstrap_of_identical_trades_has_no_spread(self) -> None:
        report = MonteCarlo(config()).run(ledger("10", "10", "10", "10"))

        assert report.distribution is not None
        net = report.distribution.net_pnl
        assert (net.low, net.observed, net.high) == (D(40), D(40), D(40))
        assert report.distribution.probability_net_positive == D(1)
        assert report.distribution.profit_factor is None  # never a loss to divide by
        assert report.distribution.profit_factor_undefined == 200

    def test_scripted_bootstrap_draws_are_worked_by_hand(self) -> None:
        # source answering 0 always: the bootstrap is the first trade repeated (+100 x 3), the
        # permutation is a fixed reordering of 100, -30, -30
        report = MonteCarlo(
            config(resamples=3, min_trades=3), source_factory=lambda seed: ScriptedSource(0)
        ).run(ledger("100", "-30", "-30"))

        assert report.distribution is not None
        assert report.distribution.net_pnl.low == D(300)
        assert report.distribution.net_pnl.high == D(300)
        assert report.distribution.probability_net_positive == D(1)
        assert report.observed.net_pnl == D(40)

    def test_every_ordering_that_breaches_the_limit_makes_ruin_certain(self) -> None:
        # +100 then -500 peaks at 1100 and ends at 600 (45%); -500 first is down 50%
        ledger_ = ledger("100", "-500")

        certain = MonteCarlo(config(drawdown_limit=D("0.3"))).run(ledger_)
        never = MonteCarlo(config(drawdown_limit=D("0.6"))).run(ledger_)

        assert certain.distribution is not None and never.distribution is not None
        assert certain.distribution.probability_of_ruin == D(1)
        assert never.distribution.probability_of_ruin == D(0)

    def test_a_mostly_losing_book_is_rarely_net_positive(self) -> None:
        report = MonteCarlo(config(resamples=500, min_trades=10)).run(
            ledger(*(["-20"] * 18 + ["30"] * 2))
        )

        assert report.distribution is not None
        assert report.distribution.probability_net_positive < D("0.05")
        assert report.distribution.net_pnl.high < D(0) or report.distribution.net_pnl.low < D(0)


class TestReproducibility:
    def test_same_trades_and_seed_give_the_same_report(self) -> None:
        trades = ledger("100", "-40", "60", "-20", "-80", "30")

        assert MonteCarlo(config()).run(trades) == MonteCarlo(config()).run(trades)

    def test_the_input_order_of_trades_does_not_matter(self) -> None:
        trades = ledger("100", "-40", "60", "-20", "-80", "30")

        assert MonteCarlo(config()).run(trades) == MonteCarlo(config()).run(trades[::-1])

    def test_another_seed_draws_another_sample(self) -> None:
        trades = ledger("100", "-40", "60", "-20", "-80", "30", "15", "-5")

        first = MonteCarlo(config(seed=1)).run(trades).distribution
        second = MonteCarlo(config(seed=2)).run(trades).distribution

        assert first != second


class TestTooFewTrades:
    def test_thin_evidence_reports_observed_figures_and_no_interval(self) -> None:
        report = MonteCarlo(config(min_trades=30)).run(ledger("100", "-40", "60"))

        assert not report.conclusive
        assert report.distribution is None
        assert report.inconclusive_reason is not None and "3 trades" in report.inconclusive_reason
        assert report.observed.net_pnl == D(120)

    def test_an_empty_run_is_inconclusive_not_an_error(self) -> None:
        report = MonteCarlo(config(min_trades=1)).run([])

        assert not report.conclusive
        assert report.observed.expectancy is None


class TestConfigValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"resamples": 0},
            {"confidence": D(1)},
            {"confidence": D(0)},
            {"starting_equity": D(0)},
            {"drawdown_limit": D(0)},
            {"drawdown_limit": D("1.5")},
            {"min_trades": 0},
        ],
    )
    def test_refuses_nonsense(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            config(**overrides)

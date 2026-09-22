"""The selection criteria judge only out-of-sample money, and every check can fail on its own."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.curation import (
    CurationReport,
    PooledOutcome,
    SelectionCriteria,
    StrategyCurator,
)
from emporos.backtest.metrics.trades import TradeAnalyzer
from emporos.backtest.portfolio import ClosedTrade, TradeDirection
from emporos.domain.money import Money

T0 = datetime(2026, 3, 2, 4, 0, tzinfo=UTC)


def trade(n: int, net: str, fees: str = "10", symbol: str = "NSE:1") -> ClosedTrade:
    gross = Decimal(net) + Decimal(fees)
    return ClosedTrade(
        symbol, TradeDirection.LONG, 10, T0 + timedelta(minutes=n), T0 + timedelta(minutes=n + 3),
        Money.of("100"), Money.of("101"), Money(gross), Money.of(fees), "run-1",
    )  # fmt: skip


def pooled(trades: list[ClosedTrade], windows: list[str], drawdowns: list[str] | None = None,
           symbols: dict[str, str] | None = None) -> PooledOutcome:  # fmt: skip
    return PooledOutcome(
        trades=tuple(trades),
        statistics=TradeAnalyzer().analyze(trades),
        window_nets=tuple(Decimal(w) for w in windows),
        window_drawdowns=tuple(Decimal(d) for d in (drawdowns or ["0.02"] * len(windows))),
        symbol_nets={k: Decimal(v) for k, v in (symbols or {"a": "1", "b": "1"}).items()},
        net_at_double_costs=sum(
            (t.gross_pnl.amount - 2 * t.fees.amount for t in trades), Decimal(0)
        ),
        chosen=tuple("c" for _ in windows),
    )  # fmt: skip


def good() -> PooledOutcome:
    wins = [trade(n, "100") for n in range(0, 200, 2)]  # 100 wins of 100
    losses = [trade(n, "-50") for n in range(1, 200, 2)]  # 100 losses of 50
    return pooled(wins + losses, ["2500", "2500", "2500", "-2500", "2500"])


CRITERIA = SelectionCriteria()


def failed(outcome: PooledOutcome) -> set[str]:
    return {c.name for c in CRITERIA.judge(outcome).failures}


class TestCriteria:
    def test_a_strategy_that_clears_every_bar_passes(self) -> None:
        verdict = CRITERIA.judge(good())
        assert verdict.passed and verdict.failures == () and len(verdict.checks) == 8

    def test_a_loss_fails_the_first_check(self) -> None:
        losers = [trade(n, "-5") for n in range(200)]
        assert "net profit is positive" in failed(pooled(losers, ["-1"] * 5))

    def test_too_few_trades_is_not_evidence(self) -> None:
        few = [trade(n, "100") for n in range(10)] + [trade(n, "-10") for n in range(10, 20)]
        assert "enough trades to mean something" in failed(pooled(few, ["10"] * 5))

    def test_a_low_profit_factor_fails_even_when_net_is_positive(self) -> None:
        thin = [trade(n, "11") for n in range(0, 200, 2)] + [
            trade(n, "-10") for n in range(1, 200, 2)
        ]
        outcome = pooled(thin, ["100"] * 5)
        assert outcome.statistics.net_pnl.amount > 0 and "profit factor" in failed(outcome)

    def test_a_profit_that_lives_in_one_window_is_not_robust(self) -> None:
        outcome = replace(
            good(), window_nets=tuple(Decimal(x) for x in ("9000", "-500", "-500", "-500", "-500"))
        )
        assert {"profitable in most out-of-sample windows"} <= failed(outcome)

    def test_dropping_the_best_window_must_leave_a_profit(self) -> None:
        base = good()
        outcome = replace(
            base,
            window_nets=tuple(Decimal(x) for x in ("600", "-100", "-100", "-100", "-200")),
            statistics=replace(base.statistics, net_pnl=Money.of("100")),
        )  # profitable overall (100), but only because of one window (600)
        assert "still profitable without its best window" in failed(outcome)

    def test_a_deep_drawdown_in_any_window_fails(self) -> None:
        outcome = replace(
            good(),
            window_drawdowns=tuple(Decimal(x) for x in ("0.02", "0.02", "0.31", "0.02", "0.02")),
        )
        assert "no window's drawdown is too deep" in failed(outcome)

    def test_profit_concentrated_in_few_instruments_fails(self) -> None:
        outcome = replace(
            good(),
            symbol_nets={"a": Decimal(900), "b": Decimal(-1), "c": Decimal(-1), "d": Decimal(-1)},
        )
        assert "profitable across instruments, not a few" in failed(outcome)

    def test_a_strategy_that_only_works_at_todays_costs_fails_the_stress(self) -> None:
        thin = [trade(n, "20", fees="15") for n in range(0, 200, 2)] + [
            trade(n, "-10", fees="15") for n in range(1, 200, 2)
        ]
        outcome = pooled(thin, ["100"] * 5)
        assert outcome.statistics.net_pnl.amount > 0 and outcome.net_at_double_costs < 0
        assert "still profitable if charges were twice as high" in failed(outcome)

    def test_the_optional_checks_can_be_switched_off_only_explicitly(self) -> None:
        lenient = SelectionCriteria(
            require_profit_without_best_window=False, require_profit_at_double_costs=False
        )
        assert len(lenient.judge(good()).checks) == 6

    def test_no_windows_or_no_symbols_cannot_pass(self) -> None:
        empty = pooled([], [], symbols={})
        assert not CRITERIA.judge(empty).passed


class TestReport:
    def test_failures_are_reported_as_prominently_as_passes(self) -> None:
        from emporos.backtest.curation import CurationRecord

        windows = (("2026-01-30", "2026-03-13", "a"),)
        record = CurationRecord(
            "orb_v1", ("a", "b"), good(), CRITERIA.judge(good()), Decimal("0.05"), windows
        )
        bad = pooled([trade(n, "-5") for n in range(200)], ["-1"] * 5)
        record_bad = CurationRecord(
            "vwap_v1", ("a",), bad, CRITERIA.judge(bad), Decimal("-0.2"), ()
        )
        text = CurationReport().render([record, record_bad], CRITERIA)
        assert "## orb_v1: PASSED" in text and "## vwap_v1: FAILED" in text
        assert "| net profit is positive | -1000.00 | > 0 | FAIL |" in text
        assert (
            "fixed before any result was seen" in text and "2026-01-30 to 2026-03-13: `a`" in text
        )

    def test_the_curator_judges_only_what_the_walk_forward_returned(self) -> None:
        assert StrategyCurator(CRITERIA)  # constructed from criteria alone: it holds no data
        pytest.importorskip("emporos.backtest.walkforward_run")

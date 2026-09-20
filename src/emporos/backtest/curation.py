"""Strategy curation: which strategies earn a place, judged on money they did NOT get to tune on.

The rules are written down BEFORE any result is seen (`SelectionCriteria`, committed first) and a
strategy is called profitable only if its walk-forward OUT-OF-SAMPLE trades — every parameter chosen
on data that ended before the test began, every test window scored once — clear all of them, net of
the real charges, with the platform's risk rules in the loop. A strategy that does not is reported
as failed, with which check it failed. Nothing here can turn a failure into a pass: `judge` only
reads the pooled out-of-sample trades.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO
from emporos.backtest.metrics.trades import TradeAnalyzer, TradeStatistics
from emporos.backtest.portfolio import ClosedTrade
from emporos.backtest.walkforward_run import WalkForwardResult

_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    actual: str
    required: str


@dataclass(frozen=True)
class Verdict:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True)
class PooledOutcome:
    """Everything the criteria may look at: the out-of-sample trades and per-window figures."""

    trades: tuple[ClosedTrade, ...]
    statistics: TradeStatistics
    window_nets: tuple[Decimal, ...]
    window_drawdowns: tuple[Decimal, ...]  # each a fraction of that window's peak equity
    symbol_nets: dict[str, Decimal]
    net_at_double_costs: Decimal
    chosen: tuple[str, ...]  # the candidate picked in each window

    @classmethod
    def of(cls, result: WalkForwardResult) -> PooledOutcome:
        trades = tuple(t for outcome in result.outcomes for t in outcome.test.trades)
        by_symbol: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for trade in trades:
            by_symbol[trade.instrument_id] += trade.net_pnl.amount
        return cls(
            trades=trades,
            statistics=TradeAnalyzer().analyze(trades),
            window_nets=tuple(o.test.metrics.trades.net_pnl.amount for o in result.outcomes),
            window_drawdowns=tuple(o.test.metrics.drawdown.max_drawdown for o in result.outcomes),
            symbol_nets=dict(by_symbol),
            net_at_double_costs=sum((t.gross_pnl.amount - 2 * t.fees.amount for t in trades), ZERO),
            chosen=tuple(o.chosen.name for o in result.outcomes),
        )


@dataclass(frozen=True)
class SelectionCriteria:
    """Fixed in advance. Loosening any of these after seeing a result defeats the purpose."""

    min_trades: int = 150
    min_profit_factor: Decimal = Decimal("1.2")
    min_positive_window_share: Decimal = Decimal("0.6")
    max_window_drawdown: Decimal = Decimal("0.10")  # a fraction of starting cash, worst window
    min_positive_symbol_share: Decimal = Decimal("0.5")
    require_profit_without_best_window: bool = True
    require_profit_at_double_costs: bool = True

    def judge(self, pooled: PooledOutcome) -> Verdict:
        stats = pooled.statistics
        total = stats.net_pnl.amount
        windows = len(pooled.window_nets)
        positive_windows = sum(1 for n in pooled.window_nets if n > ZERO)
        symbols = len(pooled.symbol_nets)
        positive_symbols = sum(1 for n in pooled.symbol_nets.values() if n > ZERO)
        without_best = total - max(pooled.window_nets, default=ZERO)
        worst_drawdown = max(pooled.window_drawdowns, default=ZERO)
        checks = [
            Check("net profit is positive", total > ZERO, f"{total:.2f}", "> 0"),
            Check(
                "enough trades to mean something",
                stats.count >= self.min_trades,
                str(stats.count),
                f">= {self.min_trades}",
            ),
            Check(
                "profit factor",
                stats.profit_factor is not None and stats.profit_factor >= self.min_profit_factor,
                "n/a" if stats.profit_factor is None else f"{stats.profit_factor:.3f}",
                f">= {self.min_profit_factor}",
            ),
            Check(
                "profitable in most out-of-sample windows",
                windows > 0
                and Decimal(positive_windows) / windows >= self.min_positive_window_share,
                f"{positive_windows} of {windows}",
                f">= {self.min_positive_window_share:.0%} of windows",
            ),
            Check(
                "no window's drawdown is too deep",
                worst_drawdown <= self.max_window_drawdown,
                f"{worst_drawdown * _HUNDRED:.1f}%",
                f"<= {self.max_window_drawdown * _HUNDRED:.0f}%",
            ),
            Check(
                "profitable across instruments, not a few",
                symbols > 0
                and Decimal(positive_symbols) / symbols >= self.min_positive_symbol_share,
                f"{positive_symbols} of {symbols}",
                f">= {self.min_positive_symbol_share:.0%} of instruments",
            ),
        ]
        if self.require_profit_without_best_window:
            checks.append(
                Check(
                    "still profitable without its best window",
                    without_best > ZERO,
                    f"{without_best:.2f}",
                    "> 0",
                )
            )
        if self.require_profit_at_double_costs:
            checks.append(
                Check(
                    "still profitable if charges were twice as high",
                    pooled.net_at_double_costs > ZERO,
                    f"{pooled.net_at_double_costs:.2f}",
                    "> 0",
                )
            )
        return Verdict(tuple(checks))


@dataclass(frozen=True)
class CurationRecord:
    strategy: str
    candidates: tuple[str, ...]
    pooled: PooledOutcome
    verdict: Verdict
    compounded_return: Decimal
    windows: tuple[tuple[str, str, str], ...]  # (test start, test end, chosen candidate)


class StrategyCurator:
    """Judges one strategy's walk-forward result. Pure: no I/O, no way to influence the result."""

    def __init__(self, criteria: SelectionCriteria) -> None:
        self._criteria = criteria

    def evaluate(
        self, strategy: str, candidates: Sequence[str], result: WalkForwardResult
    ) -> CurationRecord:
        pooled = PooledOutcome.of(result)
        return CurationRecord(
            strategy=strategy,
            candidates=tuple(candidates),
            pooled=pooled,
            verdict=self._criteria.judge(pooled),
            compounded_return=result.compounded_test_return,
            windows=tuple(
                (
                    o.window.test.start.date().isoformat(),
                    o.window.test.end.date().isoformat(),
                    o.chosen.name,
                )
                for o in result.outcomes
            ),
        )


class CurationReport:
    """A human-readable account of every strategy tried, the failures first-class."""

    def render(self, records: Iterable[CurationRecord], criteria: SelectionCriteria) -> str:
        lines = [
            "# Strategy curation",
            "",
            "Selection criteria (fixed before any result was seen):",
            "",
        ]
        lines += [
            f"- at least {criteria.min_trades} out-of-sample trades, profit factor >= "
            f"{criteria.min_profit_factor}, net profit > 0",
            f"- profitable in >= {criteria.min_positive_window_share:.0%} of out-of-sample windows "
            f"and >= {criteria.min_positive_symbol_share:.0%} of instruments",
            f"- worst window drawdown <= {criteria.max_window_drawdown * _HUNDRED:.0f}%",
            "- still profitable without its best window, and with charges doubled",
            "",
        ]
        for record in records:
            stats = record.pooled.statistics
            lines += [
                f"## {record.strategy}: {'PASSED' if record.verdict.passed else 'FAILED'}",
                "",
                f"Out-of-sample: {stats.count} trades, net {stats.net_pnl.amount:.2f} "
                f"(gross {stats.gross_pnl.amount:.2f}, charges {stats.fees.amount:.2f}), "
                f"win rate {_pct(stats.win_rate)}, profit factor {_num(stats.profit_factor)}, "
                f"compounded return {record.compounded_return * _HUNDRED:.2f}%.",
                "",
                "| check | actual | required | |",
                "|---|---|---|---|",
            ]
            lines += [
                f"| {c.name} | {c.actual} | {c.required} | {'ok' if c.passed else 'FAIL'} |"
                for c in record.verdict.checks
            ]
            lines += ["", "Parameters chosen per test window (each on its own training data):", ""]
            lines += [f"- {start} to {end}: `{name}`" for start, end, name in record.windows]
            lines.append("")
        return "\n".join(lines)


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value * _HUNDRED:.1f}%"


def _num(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"

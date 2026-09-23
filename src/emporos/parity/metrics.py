"""Degradation: how much worse (or different) paper was than the backtest of the same day.

`SideTotals` holds the raw, additive facts for one side over any slice of rows (a symbol, a
session, a whole period); every ratio is a property of them, so a weekly figure is the ratio of
summed facts, never an average of daily ratios. Both sides are built by the SAME code, and a trip
is measured on realised net P&L on both, so a definition cannot differ between them.

Sign convention: `Delta.absolute` is paper minus backtest. For fill rate, expectancy, win rate and
P&L a NEGATIVE delta is degradation; for slippage, cost, drawdown and turnover a POSITIVE one is.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.metrics.drawdown import DrawdownAnalyzer
from emporos.backtest.portfolio import EquityPoint
from emporos.domain.money import Money
from emporos.parity.ledger import SessionParity, SignalParity, TradeFacts, TradePair
from emporos.parity.models import SideOutcome

Side = Literal["paper", "backtest"]
_BPS = Decimal(10_000)
_P90 = Decimal("0.9")


@dataclass(frozen=True)
class SideTotals:
    orders: int  # signals that produced an order
    filled_orders: int  # of those, the ones that traded at all
    trades: int
    wins: int
    gross_pnl: Decimal
    net_pnl: Decimal
    charges: Decimal  # every fill's charges, whether or not the trip closed
    notional: Decimal  # traded value
    return_sum: Decimal  # sum over trips of net P&L / entry notional
    slippage_bps: tuple[Decimal, ...]
    worst_drawdown: Decimal  # deepest realised drawdown of any session, a fraction of its peak

    @property
    def fill_rate(self) -> Decimal | None:
        return (
            None
            if not self.orders
            else DecimalMath.divide(Decimal(self.filled_orders), Decimal(self.orders))
        )

    @property
    def win_rate(self) -> Decimal | None:
        return (
            None
            if not self.trades
            else DecimalMath.divide(Decimal(self.wins), Decimal(self.trades))
        )

    @property
    def expectancy(self) -> Decimal | None:
        return (
            None if not self.trades else DecimalMath.divide(self.return_sum, Decimal(self.trades))
        )

    @property
    def slippage_mean_bps(self) -> Decimal | None:
        return DecimalMath.mean(self.slippage_bps) if self.slippage_bps else None

    @property
    def slippage_p90_bps(self) -> Decimal | None:
        if not self.slippage_bps:
            return None
        ordered = sorted(self.slippage_bps)
        rank = -(-len(ordered) * _P90 // 1)  # nearest rank, ceiling
        return ordered[max(int(rank) - 1, 0)]

    @property
    def cost_per_trade(self) -> Decimal | None:
        return None if not self.trades else DecimalMath.divide(self.charges, Decimal(self.trades))

    @property
    def cost_bps(self) -> Decimal | None:
        return (
            None
            if self.notional <= ZERO
            else DecimalMath.divide(self.charges, self.notional) * _BPS
        )


class ParityMetric(StrEnum):
    FILL_RATE = "fill_rate"
    SLIPPAGE_MEAN_BPS = "slippage_mean_bps"
    SLIPPAGE_P90_BPS = "slippage_p90_bps"
    TURNOVER = "turnover"
    EXPECTANCY = "expectancy"
    WIN_RATE = "win_rate"
    MAX_DRAWDOWN = "max_drawdown"
    NET_PNL = "net_pnl"
    CHARGES = "charges"
    COST_PER_TRADE = "cost_per_trade"
    COST_BPS = "cost_bps"


@dataclass(frozen=True)
class Delta:
    backtest: Decimal | None
    paper: Decimal | None

    @property
    def absolute(self) -> Decimal | None:
        if self.backtest is None or self.paper is None:
            return None
        return self.paper - self.backtest

    @property
    def relative(self) -> Decimal | None:
        """Paper's change as a fraction of the backtest's magnitude; None when there is no base."""
        absolute = self.absolute
        if absolute is None or not self.backtest:
            return None
        return DecimalMath.divide(absolute, abs(self.backtest))


_EXTRACT: dict[ParityMetric, Callable[[SideTotals], Decimal | None]] = {
    ParityMetric.FILL_RATE: lambda t: t.fill_rate,
    ParityMetric.SLIPPAGE_MEAN_BPS: lambda t: t.slippage_mean_bps,
    ParityMetric.SLIPPAGE_P90_BPS: lambda t: t.slippage_p90_bps,
    ParityMetric.TURNOVER: lambda t: t.notional,
    ParityMetric.EXPECTANCY: lambda t: t.expectancy,
    ParityMetric.WIN_RATE: lambda t: t.win_rate,
    ParityMetric.MAX_DRAWDOWN: lambda t: t.worst_drawdown,
    ParityMetric.NET_PNL: lambda t: t.net_pnl,
    ParityMetric.CHARGES: lambda t: t.charges,
    ParityMetric.COST_PER_TRADE: lambda t: t.cost_per_trade,
    ParityMetric.COST_BPS: lambda t: t.cost_bps,
}


@dataclass(frozen=True)
class ParityMetrics:
    """Every metric, both sides, with the sample it rests on."""

    paper: SideTotals
    backtest: SideTotals
    signals: int
    matched_signals: int  # signalled by both sides
    matched_trades: int  # round trips present on both sides
    sessions: int
    signal_agreement: Decimal | None  # both / (both + paper-only + backtest-only)

    def delta(self, metric: ParityMetric) -> Delta:
        pull = _EXTRACT[metric]
        return Delta(pull(self.backtest), pull(self.paper))

    def all(self) -> dict[ParityMetric, Delta]:
        return {metric: self.delta(metric) for metric in ParityMetric}

    @property
    def turnover_ratio(self) -> Decimal | None:
        """Paper's traded value over the backtest's."""
        if self.backtest.notional <= ZERO:
            return None
        return DecimalMath.divide(self.paper.notional, self.backtest.notional)


class TotalsBuilder:
    """Builds one side's totals from rows: the only place that decides what a metric means."""

    def __init__(self, drawdowns: DrawdownAnalyzer | None = None) -> None:
        self._drawdowns = drawdowns or DrawdownAnalyzer()

    def of(self, side: Side, sessions: Iterable[SessionParity]) -> SideTotals:
        orders = filled = 0
        notional = ZERO
        charges = ZERO
        slippage: list[Decimal] = []
        trades = wins = 0
        gross = net = returns = ZERO
        worst = ZERO
        for session in sessions:
            for row in session.signals:
                outcome = self._outcome(side, row)
                if outcome is None:
                    continue
                charges += outcome.charges.amount
                if outcome.ordered_quantity > 0:
                    orders += 1
                if outcome.filled_quantity > 0:
                    filled += 1
                    if outcome.average_fill_price is not None:
                        notional += outcome.average_fill_price.amount * outcome.filled_quantity
                if outcome.slippage is not None:
                    slippage.append(outcome.slippage.bps)
            facts = [f for f in (self._trade(side, t) for t in session.trades) if f is not None]
            trades += len(facts)
            wins += sum(1 for f in facts if f.net_pnl > ZERO)
            gross += sum((f.gross_pnl for f in facts), ZERO)
            net += sum((f.net_pnl for f in facts), ZERO)
            returns += sum(
                (
                    DecimalMath.divide(f.net_pnl, f.entry_notional)
                    for f in facts
                    if f.entry_notional
                ),
                ZERO,
            )
            worst = max(worst, self._drawdown(session.starting_cash, facts))
        return SideTotals(
            orders, filled, trades, wins, gross, net, charges, notional, returns,
            tuple(slippage), worst,
        )  # fmt: skip

    def _drawdown(self, starting_cash: Decimal, facts: Sequence[TradeFacts]) -> Decimal:
        equity = starting_cash
        curve: list[EquityPoint] = []
        for fact in sorted(facts, key=lambda f: f.closed_at):
            equity += fact.net_pnl
            curve.append(EquityPoint(fact.closed_at, Money(equity), Money.zero(), 0))
        return self._drawdowns.analyze(starting_cash, curve).max_drawdown

    @staticmethod
    def _outcome(side: Side, row: SignalParity) -> SideOutcome | None:
        return row.paper_outcome if side == "paper" else row.backtest_outcome

    @staticmethod
    def _trade(side: Side, pair: TradePair) -> TradeFacts | None:
        return pair.paper if side == "paper" else pair.backtest


@dataclass(frozen=True)
class ParitySlices:
    """The same metrics cut the four ways the acceptance criteria ask for."""

    overall: ParityMetrics
    by_strategy: dict[str, ParityMetrics] = field(default_factory=dict)
    by_symbol: dict[str, ParityMetrics] = field(default_factory=dict)
    by_session: dict[date, ParityMetrics] = field(default_factory=dict)
    by_trade: tuple[TradePair, ...] = ()


class ParityAnalyzer:
    def __init__(self, totals: TotalsBuilder | None = None) -> None:
        self._totals = totals or TotalsBuilder()

    def metrics(self, sessions: Sequence[SessionParity]) -> ParityMetrics:
        rows = [row for s in sessions for row in s.signals]
        return self._metrics(sessions, rows, [t for s in sessions for t in s.trades])

    def slices(self, sessions: Sequence[SessionParity]) -> ParitySlices:
        by_strategy: dict[str, list[SessionParity]] = defaultdict(list)
        by_date: dict[date, list[SessionParity]] = defaultdict(list)
        for session in sessions:
            by_strategy[session.strategy].append(session)
            by_date[session.session_date].append(session)
        symbols = sorted(
            {r.instrument_id for s in sessions for r in s.signals}
            | {t.instrument_id for s in sessions for t in s.trades}
        )
        return ParitySlices(
            overall=self.metrics(sessions),
            by_strategy={k: self.metrics(v) for k, v in sorted(by_strategy.items())},
            by_symbol={sym: self._of_symbol(sessions, sym) for sym in symbols},
            by_session={k: self.metrics(v) for k, v in sorted(by_date.items())},
            by_trade=tuple(t for s in sessions for t in s.trades),
        )

    def _of_symbol(self, sessions: Sequence[SessionParity], symbol: str) -> ParityMetrics:
        narrowed = [
            SessionParity(
                s.strategy, s.run_id, s.session_date, s.behaviour_hash, s.starting_cash,
                tuple(r for r in s.signals if r.instrument_id == symbol),
                tuple(t for t in s.trades if t.instrument_id == symbol),
            )
            for s in sessions
        ]  # fmt: skip
        return self.metrics(narrowed)

    def _metrics(
        self,
        sessions: Sequence[SessionParity],
        rows: Sequence[SignalParity],
        trades: Sequence[TradePair],
    ) -> ParityMetrics:
        both = sum(1 for r in rows if r.paper is not None and r.backtest is not None)
        one_sided = len(rows) - both  # every row has at least one side
        agreed = both
        return ParityMetrics(
            paper=self._totals.of("paper", sessions),
            backtest=self._totals.of("backtest", sessions),
            signals=len(rows),
            matched_signals=agreed,
            matched_trades=sum(1 for t in trades if t.paper is not None and t.backtest is not None),
            sessions=len(sessions),
            signal_agreement=None
            if not (agreed + one_sided)
            else DecimalMath.divide(Decimal(agreed), Decimal(agreed + one_sided)),
        )

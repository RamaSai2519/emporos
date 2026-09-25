"""The replay's book: every trade with its full path, and the risk snapshot at any instant (EM-240).

A replay knows a trade's exit when it enters it, so the book answers "what was open, and what was
realised, at time t" from the trades' own times. Marks come from the market's last completed bar."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from emporos.core.clock import IST
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.market import MarketData
from emporos.eventtrader.replay.records import TradeRecord
from emporos.eventtrader.risk.models import OpenPosition, Product, RiskSnapshot
from emporos.eventtrader.stages.models import Side

__all__ = ["Book"]


def _pnl(trade: TradeRecord, price: Decimal) -> Decimal:
    move = (price - trade.entry_price) * trade.quantity
    return move if trade.side is Side.LONG else -move


class Book:
    def __init__(self, starting_capital: Decimal, market: MarketData) -> None:
        self._capital, self._market = starting_capital, market
        self._trades: list[TradeRecord] = []

    @property
    def trades(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trades)

    def add(self, trade: TradeRecord) -> None:
        self._trades.append(trade)

    def snapshot(self, at: datetime, posture_scale: Decimal) -> RiskSnapshot:
        """Everything the rules may read at `at`: what has been realised (net at benchmark costs,
        token cost excluded: the loss budget is trading money) and what is open."""
        today = at.astimezone(IST).date()
        realized_total = Decimal(0)
        realized_today = Decimal(0)
        open_positions: list[OpenPosition] = []
        for trade in self._trades:
            if trade.exit_ts <= at:
                realized_total += trade.net_benchmark
                if trade.exit_ts.astimezone(IST).date() == today:
                    realized_today += trade.net_benchmark
            elif trade.entry_ts <= at:
                open_positions.append(self._open(trade, at))
        return RiskSnapshot(
            at, self._capital, realized_total, realized_today, tuple(open_positions), posture_scale
        )

    def close_all(self, at: datetime) -> int:
        """The total-loss kill: every trade still open at `at` is closed at its last mark."""
        closed = 0
        for i, trade in enumerate(self._trades):
            if trade.entry_ts <= at < trade.exit_ts:
                price = self._mark(trade, at)
                gross = _pnl(trade, price)
                self._trades[i] = replace(
                    trade, exit_ts=at, exit_price=price, exit_reason=ExitReason.KILL,
                    gross_pnl=gross,
                )  # fmt: skip
                closed += 1
        return closed

    def _open(self, trade: TradeRecord, at: datetime) -> OpenPosition:
        return OpenPosition(
            trade.name, trade.product, trade.side, trade.quantity, trade.entry_price,
            trade.stop_price, trade.risk, _pnl(trade, self._mark(trade, at)),
        )  # fmt: skip

    def _mark(self, trade: TradeRecord, at: datetime) -> Decimal:
        """The last completed 5-minute close. An option has no intraday price here: it is carried
        at its entry premium until its exit."""
        if trade.product is Product.OPTION:
            return trade.entry_price
        return self._market.last_close(trade.name, at) or trade.entry_price

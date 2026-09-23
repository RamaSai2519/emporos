"""Round trips on the paper side, booked exactly as the backtest books its own.

Paper executions are applied to the same `BacktestPortfolio` (over `PaperAccount`) a backtest uses,
in the order they happened, so a round trip means the same thing on both sides: flat back to flat,
charges included. `TradePairer` then lines the two sides' trips up.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from emporos.backtest.orders import Fill
from emporos.backtest.portfolio import BacktestPortfolio, ClosedTrade, TradeDirection
from emporos.domain.money import Money
from emporos.parity.inputs import PaperRunData
from emporos.parity.ledger import TradeFacts, TradePair


class PaperTrades:
    def build(self, data: PaperRunData, starting_cash: Money) -> tuple[ClosedTrade, ...]:
        orders = {o.id: o for o in data.orders}
        portfolio = BacktestPortfolio(starting_cash)
        ordered = sorted(
            data.executions, key=lambda x: (x.ts, x.session_trade_no or 0, x.broker_trade_id)
        )
        for number, execution in enumerate(ordered, start=1):
            order = orders.get(execution.order_id)
            portfolio.apply(
                Fill(
                    number, execution.order_id, order.ordertag if order else execution.order_id,
                    execution.instrument_id, execution.side, execution.quantity, execution.price,
                    execution.ts, (order.strategy_run_id if order else None) or data.run.id,
                ),
                execution.fees or Money.zero(),
            )  # fmt: skip
        return portfolio.closed_trades


class TradePairer:
    """Pairs the n-th trip of each (instrument, direction) on one side with the n-th on the other,
    in the order they opened. A signal-level match already explains WHY two trips differ; this
    only decides which trips to set side by side."""

    def pair(
        self, paper: Sequence[ClosedTrade], backtest: Sequence[ClosedTrade]
    ) -> tuple[TradePair, ...]:
        left = self._grouped(paper)
        right = self._grouped(backtest)
        pairs: list[TradePair] = []
        for key in sorted(left.keys() | right.keys(), key=lambda k: (k[0], k[1].value)):
            mine, theirs = left.get(key, []), right.get(key, [])
            for index in range(max(len(mine), len(theirs))):
                pairs.append(
                    TradePair(
                        key[0],
                        key[1],
                        TradeFacts.of(mine[index]) if index < len(mine) else None,
                        TradeFacts.of(theirs[index]) if index < len(theirs) else None,
                    )  # fmt: skip
                )
        return tuple(pairs)

    @staticmethod
    def _grouped(
        trades: Sequence[ClosedTrade],
    ) -> dict[tuple[str, TradeDirection], list[ClosedTrade]]:
        grouped: dict[tuple[str, TradeDirection], list[ClosedTrade]] = defaultdict(list)
        for trade in sorted(trades, key=lambda t: (t.opened_at, t.closed_at)):
            grouped[(trade.instrument_id, trade.direction)].append(trade)
        return grouped

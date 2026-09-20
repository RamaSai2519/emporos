"""Each strategy's own positions (EM-99 H4), built from the fills its orders produced.

Portfolio positions are per ACCOUNT. A strategy must only see what IT holds, so this book replays
the executions of the orders that carry its run id. It is rebuilt from persisted fills at start-up
and kept current by the fill processor's listener hook, so a restart shows the same positions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from emporos.domain.money import Money
from emporos.domain.positions import Position
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.portfolio.ledger import PositionCalculator
from emporos.strategies.positions import PositionView


class StrategyPositionBook:
    def __init__(self, account_id: str, calculator: PositionCalculator) -> None:
        self._account_id = account_id
        self._calculator = calculator
        self._positions: dict[tuple[str, str], PositionRecord] = {}
        self._applied: set[str] = set()

    def load(
        self, executions: Iterable[ExecutionRecord], orders: Mapping[str, OrderRecord]
    ) -> None:
        """Rebuild from history: `orders` maps order id to its record."""
        for execution in executions:
            order = orders.get(execution.order_id)
            if order is not None:
                self.on_fill(order, execution)

    def on_fill(self, order: OrderRecord, execution: ExecutionRecord) -> None:
        run = order.strategy_run_id
        if run is None or execution.broker_trade_id in self._applied:
            return  # not a strategy's order (manual, square-off) or already counted
        self._applied.add(execution.broker_trade_id)
        key = (run, execution.instrument_id)
        held = self._positions.get(key) or PositionRecord(
            _id=f"{run}:{execution.instrument_id}",
            account_id=self._account_id,
            instrument_id=execution.instrument_id,
            net_quantity=0,
            average_price=Money.zero(),
            realised_pnl=Money.zero(),
            updated_at=execution.ts,
        )
        self._positions[key] = self._calculator.apply(held, execution)

    def pnl_by_run(self, marks: Mapping[str, Money]) -> dict[str, Money]:
        """Each run's realised (net of charges) plus unrealised P&L. An open position with no live
        mark is valued at cost — it adds nothing rather than an invented profit or loss."""
        totals: dict[str, Money] = {}
        for (run, instrument), held in self._positions.items():
            value = held.realised_pnl
            mark = marks.get(instrument)
            if held.net_quantity and mark is not None:
                value = value + (mark - held.average_price).times(held.net_quantity)
            totals[run] = totals.get(run, Money.zero()) + value
        return totals

    def view_for(self, run_id: str) -> PositionView:
        return _RunView(run_id, self._positions)


class _RunView:
    def __init__(self, run_id: str, positions: dict[tuple[str, str], PositionRecord]) -> None:
        self._run_id = run_id
        self._positions = positions

    def position(self, instrument_id: str) -> Position:
        held = self._positions.get((self._run_id, instrument_id))
        if held is None:
            return Position.flat(instrument_id)
        return Position(instrument_id, held.net_quantity, held.average_price)

    def open_positions(self) -> tuple[Position, ...]:
        return tuple(
            Position(p.instrument_id, p.net_quantity, p.average_price)
            for (run, _), p in sorted(self._positions.items())
            if run == self._run_id and p.net_quantity
        )

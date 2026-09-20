"""Rebuild positions from the fill history alone, independently of the stored position rows.

The stored position is a running total kept up to date fill by fill. Replaying every execution from
nothing must give the same answer; when it does not, one of the two is wrong and money is at risk.
This is what makes reconciliation meaningful in paper mode, where there is no outside book to
compare with, and what lets a snapshot be audited after the fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from emporos.domain.money import Money
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.portfolio.ledger import PositionCalculator


class PositionReplay:
    def __init__(self, calculator: PositionCalculator) -> None:
        self._calculator = calculator

    def replay(
        self, account_id: str, executions: Iterable[ExecutionRecord]
    ) -> Mapping[str, PositionRecord]:
        positions: dict[str, PositionRecord] = {}
        for fill in executions:
            held = positions.get(fill.instrument_id) or PositionRecord(
                _id=f"{account_id}:{fill.instrument_id}",
                account_id=account_id,
                instrument_id=fill.instrument_id,
                net_quantity=0,
                average_price=Money.zero(),
                realised_pnl=Money.zero(),
                updated_at=fill.ts,
                gross_realised_pnl=Money.zero(),
                fees=Money.zero(),
            )
            positions[fill.instrument_id] = self._calculator.apply(held, fill)
        return positions

    @staticmethod
    def filled_by_order(executions: Iterable[ExecutionRecord]) -> Mapping[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for fill in executions:
            totals[fill.order_id] += fill.quantity
        return totals

    @staticmethod
    def differences(
        stored: Iterable[PositionRecord], replayed: Mapping[str, PositionRecord]
    ) -> list[tuple[str, str]]:
        """(instrument, what differs) for every stored position that its own history contradicts."""
        found: list[tuple[str, str]] = []
        held = {p.instrument_id: p for p in stored}
        for instrument in sorted(held.keys() | replayed.keys()):
            ours, theirs = held.get(instrument), replayed.get(instrument)
            if ours is None or theirs is None:
                found.append((instrument, "position row and fill history disagree on existence"))
            elif _facts(ours) != _facts(theirs):
                found.append((instrument, f"stored {_facts(ours)} but fills give {_facts(theirs)}"))
        return found


def _facts(p: PositionRecord) -> tuple[int, Money, Money, Money]:
    return p.net_quantity, p.average_price, p.realised_pnl, p.fees or Money.zero()


def orders_disagreeing_with_fills(
    orders: Iterable[OrderRecord], filled: Mapping[str, int]
) -> list[tuple[str, str]]:
    return [
        (
            o.id,
            f"order says {o.filled_quantity} filled but its executions total {filled.get(o.id, 0)}",
        )
        for o in orders
        if o.filled_quantity != filled.get(o.id, 0)
    ]

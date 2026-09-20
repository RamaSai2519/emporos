"""The portfolio as the dashboard and the risk snapshot see it: positions, P&L, exposure.

Positions come from the fills the execution layer applied; marks come from the tick stream. This
class owns no arithmetic of its own — average-cost accounting is `PositionCalculator`, valuation is
`PortfolioValuator` — it only puts the two together for one account.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from emporos.domain.money import Money
from emporos.persistence.records import PositionRecord
from emporos.portfolio.ledger import PortfolioValuation, PortfolioValuator


class PositionSource(Protocol):
    async def open_positions(self) -> list[PositionRecord]: ...
    async def all_positions(self) -> list[PositionRecord]: ...


class MarkSource(Protocol):
    def marks(self) -> Mapping[str, Money]: ...


@dataclass(frozen=True)
class PositionView:
    instrument_id: str
    net_quantity: int
    average_price: Money
    mark: Money | None
    unrealised_pnl: Money | None  # None when there is no fresh mark: never guessed
    realised_pnl: Money
    fees: Money


@dataclass(frozen=True)
class PortfolioView:
    positions: tuple[PositionView, ...]
    valuation: PortfolioValuation
    gross_exposure: Money | None  # None when any open position lacks a mark


class PortfolioService:
    def __init__(
        self, positions: PositionSource, marks: MarkSource, valuator: PortfolioValuator
    ) -> None:
        self._positions = positions
        self._marks = marks
        self._valuator = valuator

    async def view(self) -> PortfolioView:
        records = await self._positions.all_positions()
        marks = self._marks.marks()
        views = tuple(self._position(record, marks) for record in records)
        exposure: Money | None = Money.zero()
        for view in views:
            if view.net_quantity:
                exposure = self._add(exposure, view)
        return PortfolioView(views, self._valuator.value(records, marks), exposure)

    @staticmethod
    def _add(total: Money | None, view: PositionView) -> Money | None:
        if total is None or view.mark is None:
            return None
        return total + view.mark.times(abs(view.net_quantity))

    @staticmethod
    def _position(record: PositionRecord, marks: Mapping[str, Money]) -> PositionView:
        mark = marks.get(record.instrument_id) if record.net_quantity else None
        unrealised = (
            (mark - record.average_price).times(record.net_quantity) if mark is not None else None
        )
        return PositionView(
            record.instrument_id,
            record.net_quantity,
            record.average_price,
            mark,
            unrealised if record.net_quantity else Money.zero(),
            record.realised_pnl,
            record.fees or Money.zero(),
        )

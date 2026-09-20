"""Flatten every open position at the end of the session — through the SAME path as any signal.

Square-off is not a special route to the broker: each position becomes an EXIT signal that goes
through the recorder, the risk engine and the execution engine exactly as a strategy's would, and
the repricing loop then chases any that rest. A position with no live mark cannot be priced honestly
and is reported, never guessed at.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind, SignalSink
from emporos.persistence.records import OrderRecord, PositionRecord

SQUARE_OFF_RUN = "session-square-off"


class OpenPositions(Protocol):
    async def open_positions(self) -> list[PositionRecord]: ...


class WorkingOrders(Protocol):
    async def active(self) -> list[OrderRecord]: ...


class MarkSource(Protocol):
    def marks(self) -> Mapping[str, Money]: ...


@dataclass(frozen=True)
class SquareOffReport:
    submitted: tuple[str, ...]  # instruments an exit was sent for
    already_closing: tuple[str, ...]  # an exit is already working: not duplicated
    unpriced: tuple[str, ...]  # no live mark: could not be priced


class SquareOffService:
    def __init__(
        self,
        positions: OpenPositions,
        orders: WorkingOrders,
        marks: MarkSource,
        sink: SignalSink,
        clock: Clock,
        alerts: AlertSink,
    ) -> None:
        self._positions = positions
        self._orders = orders
        self._marks = marks
        self._sink = sink
        self._clock = clock
        self._alerts = alerts

    async def flatten(self, reason: str) -> SquareOffReport:
        working = await self._orders.active()
        marks = self._marks.marks()
        submitted: list[str] = []
        closing: list[str] = []
        unpriced: list[str] = []
        for position in await self._positions.open_positions():
            side = OrderSide.SELL if position.net_quantity > 0 else OrderSide.BUY
            instrument = position.instrument_id
            if any(
                o.instrument_id == instrument and o.side is side and o.signal_kind == "EXIT"
                for o in working
            ):
                closing.append(instrument)
                continue
            mark = marks.get(instrument)
            if mark is None:
                unpriced.append(instrument)
                self._alerts.raise_alert("square_off_unpriced", f"{instrument}: no live price")
                continue
            await self._sink.submit(
                Signal(
                    strategy_run_id=SQUARE_OFF_RUN,
                    instrument_id=instrument,
                    kind=SignalKind.EXIT,
                    side=side,
                    order_type=OrderType.LIMIT,
                    quantity=abs(position.net_quantity),
                    limit_price=mark,
                    ts=self._clock.now(),
                    reason=reason,
                )
            )
            submitted.append(instrument)
        return SquareOffReport(tuple(submitted), tuple(closing), tuple(unpriced))

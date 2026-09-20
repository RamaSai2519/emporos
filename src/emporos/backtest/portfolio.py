"""The backtest's book: positions, P&L, charges, equity curve and closed round trips.

Position arithmetic is the paper broker's `PaperAccount` (average-cost positions, P&L booked when
quantity is closed, charges per instrument), reused rather than rebuilt so a backtest and a paper
session book identical fills identically. This class adds what a backtest report needs on top:

* marks: the last price seen per instrument, which values open positions;
* an equity curve, sampled by the caller (`record_point`) once per simulated moment;
* closed trades: a round trip runs from flat back to flat. A fill that flips a long into a short
  closes the trade (its charges belong to it) and opens the next one with the remainder.

Equity is `starting_cash + realised (after charges) + unrealised`. There is no margin or funds
check: a backtest is not stopped by a shortage of cash (recorded in EM-99).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from emporos.backtest.orders import Fill
from emporos.broker.models import BrokerTrade
from emporos.broker.paper.account import PaperAccount
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position

_ZERO = Money.zero()


class TradeDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class ClosedTrade:
    instrument_id: str
    direction: TradeDirection
    quantity: int  # shares entered over the whole round trip
    opened_at: datetime
    closed_at: datetime
    entry_price: Money  # average
    exit_price: Money  # average
    gross_pnl: Money
    fees: Money

    @property
    def net_pnl(self) -> Money:
        return self.gross_pnl - self.fees

    @property
    def entry_notional(self) -> Money:
        return self.entry_price.times(self.quantity)


@dataclass(frozen=True)
class EquityPoint:
    ts: datetime
    equity: Money
    gross_exposure: Money
    open_positions: int


@dataclass
class _Cycle:
    """A round trip in progress."""

    direction: TradeDirection
    opened_at: datetime
    base_gross: Money
    base_fees: Money
    entry_quantity: int = 0
    entry_turnover: Decimal = Decimal(0)
    exit_quantity: int = 0
    exit_turnover: Decimal = Decimal(0)


class BacktestPortfolio:
    """Also a `PositionView`: the strategy sees its positions through `position()` and
    `open_positions()` and nothing else."""

    def __init__(self, starting_cash: Money) -> None:
        if starting_cash <= _ZERO:
            raise ValueError("a backtest needs positive starting cash")
        self._account = PaperAccount(starting_cash)
        self._marks: dict[str, Money] = {}
        self._cycles: dict[str, _Cycle] = {}
        self._trades: list[ClosedTrade] = []
        self._curve: list[EquityPoint] = []
        self._traded_notional = Decimal(0)
        self._fill_count = 0

    # --- what the strategy sees --------------------------------------------------------------
    def position(self, instrument_id: str) -> Position:
        state = self._account.position(instrument_id)
        if state is None or state.net_quantity == 0:
            return Position.flat(instrument_id)
        return Position(instrument_id, state.net_quantity, state.average_price)

    def open_positions(self) -> tuple[Position, ...]:
        return tuple(
            Position(s.instrument_id, s.net_quantity, s.average_price)
            for s in self._account.positions()
            if s.net_quantity != 0
        )

    # --- booking -----------------------------------------------------------------------------
    def apply(self, fill: Fill, charges: Money) -> None:
        """Book one fill and the charges on it; it also marks the instrument at the fill price."""
        before = self._account.position(fill.instrument_id)
        held = 0 if before is None else before.net_quantity
        base_gross = _ZERO if before is None else before.gross_realised
        base_fees = _ZERO if before is None else before.fees
        after = self._account.apply(
            BrokerTrade(
                str(fill.sequence), fill.order_id, fill.instrument_id, fill.side, fill.quantity,
                fill.price, fill.ts,
            ),
            charges,
        )  # fmt: skip
        self._marks[fill.instrument_id] = fill.price
        self._traded_notional += fill.price.amount * fill.quantity
        self._fill_count += 1
        self._track_cycle(fill, held, after.net_quantity, base_gross, base_fees)

    def mark(self, instrument_id: str, price: Money) -> None:
        self._marks[instrument_id] = price

    def last_price(self, instrument_id: str) -> Money:
        """The latest price seen for an instrument: its last bar close or fill."""
        try:
            return self._marks[instrument_id]
        except KeyError:
            raise LookupError(f"no price has been seen for {instrument_id}") from None

    def _track_cycle(
        self, fill: Fill, held: int, now_held: int, base_gross: Money, base_fees: Money
    ) -> None:
        signed = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        cycle = self._cycles.get(fill.instrument_id)
        if cycle is None:
            direction = TradeDirection.LONG if signed > 0 else TradeDirection.SHORT
            cycle = _Cycle(direction, fill.ts, base_gross, base_fees)
            self._cycles[fill.instrument_id] = cycle
        if (signed > 0) == (cycle.direction is TradeDirection.LONG):
            cycle.entry_quantity += fill.quantity
            cycle.entry_turnover += fill.price.amount * fill.quantity
            return
        closing = min(fill.quantity, abs(held))
        cycle.exit_quantity += closing
        cycle.exit_turnover += fill.price.amount * closing
        if now_held == 0 or (now_held > 0) != (held > 0):
            self._close_cycle(fill, cycle, now_held)

    def _close_cycle(self, fill: Fill, cycle: _Cycle, now_held: int) -> None:
        state = self._account.position(fill.instrument_id)
        assert state is not None
        self._trades.append(
            ClosedTrade(
                instrument_id=fill.instrument_id,
                direction=cycle.direction,
                quantity=cycle.entry_quantity,
                opened_at=cycle.opened_at,
                closed_at=fill.ts,
                entry_price=Money(cycle.entry_turnover / cycle.entry_quantity),
                exit_price=Money(cycle.exit_turnover / cycle.exit_quantity),
                gross_pnl=state.gross_realised - cycle.base_gross,
                fees=state.fees - cycle.base_fees,
            )
        )
        del self._cycles[fill.instrument_id]
        if now_held != 0:  # flipped: the remainder opens the next round trip at this fill
            direction = TradeDirection.LONG if now_held > 0 else TradeDirection.SHORT
            opened = _Cycle(direction, fill.ts, state.gross_realised, state.fees)
            opened.entry_quantity = abs(now_held)
            opened.entry_turnover = fill.price.amount * abs(now_held)
            self._cycles[fill.instrument_id] = opened

    # --- valuation ---------------------------------------------------------------------------
    @property
    def starting_cash(self) -> Money:
        return self._account.starting_cash

    @property
    def realised(self) -> Money:
        """Booked P&L after charges."""
        return self._account.realised

    @property
    def fees(self) -> Money:
        return self._account.fees

    @property
    def unrealised(self) -> Money:
        total = _ZERO
        for state in self._account.positions():
            if state.net_quantity != 0:
                mark = self._marks.get(state.instrument_id, state.average_price)
                total = total + Money(
                    (mark.amount - state.average_price.amount) * state.net_quantity
                )
        return total

    def equity(self) -> Money:
        return self.starting_cash + self.realised + self.unrealised

    def gross_exposure(self) -> Money:
        total = _ZERO
        for state in self._account.positions():
            if state.net_quantity != 0:
                mark = self._marks.get(state.instrument_id, state.average_price)
                total = total + mark.times(abs(state.net_quantity))
        return total

    @property
    def traded_notional(self) -> Money:
        return Money(self._traded_notional)

    @property
    def fill_count(self) -> int:
        return self._fill_count

    # --- history -----------------------------------------------------------------------------
    def record_point(self, ts: datetime) -> EquityPoint:
        point = EquityPoint(ts, self.equity(), self.gross_exposure(), len(self.open_positions()))
        self._curve.append(point)
        return point

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]:
        return tuple(self._curve)

    @property
    def closed_trades(self) -> tuple[ClosedTrade, ...]:
        return tuple(self._trades)

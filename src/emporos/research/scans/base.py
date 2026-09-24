"""The shared shape of a signal scan (EM-191 F3b, EDGE_SEARCH_PLAN.md §4.1).

A scan turns one instrument's closed bars into `ScreenTrade`s, the way the backtest engine would
have traded the same rules, without building the engine. It is two small pieces:

* `ScanRules` — a strategy's entry and exit RULES as a state machine over bars. Rules know
  nothing about orders; they say "enter long", "exit", or nothing.
* `IntradayScan` — the order path every rule set shares: a signal at a bar's close becomes a
  marketable limit that can first trade on the NEXT bar, and only if that bar trades strictly
  through it (with room in its volume); the 15:15 session square-off; the broker's forced close
  at the end of the day. It mirrors `backtest.broker`, `backtest.square_off` and
  `domain.marketable`, and the parity test in `tests/unit/research/test_scan_parity.py` holds it
  to the real engine.

Prices handed back are the signal bar's close, BEFORE slippage: the screener charges the
benchmark's per-side slippage itself, which stands in for the run's own marketable-limit buffer.

What a scan does NOT model (so a screen can only be triage): the risk gate and its cross-instrument
limits (open positions, capital deployed, daily loss), partial fills (an order that a bar's volume
cannot take whole simply does not fill on that bar), and re-signals while an order is resting.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Protocol

from emporos.backtest.metrics.decimal_math import CONTEXT
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.screen_trades import ScreenTrade

__all__ = [
    "EntryIntent",
    "ExitIntent",
    "IntradayScan",
    "ScanExecution",
    "ScanIntent",
    "ScanRules",
    "SignalScan",
    "entries_open",
]

_BPS = Decimal(10_000)


def _opposite(side: OrderSide) -> OrderSide:
    return OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY


def entries_open(bar: Candle, no_new_entries_after: time) -> bool:
    """A signal from this bar is inside the entry window (`IntradayStrategy._entries_open`)."""
    return bar.closes_at.astimezone(IST).time() < no_new_entries_after


@dataclass(frozen=True)
class EntryIntent:
    side: OrderSide


@dataclass(frozen=True)
class ExitIntent:
    """Close whatever is held."""


ScanIntent = EntryIntent | ExitIntent | None


class ScanRules(Protocol):
    """One instrument's rules, fresh per scan. State lives here, updated when a signal is emitted
    (as the strategies do), not when it fills."""

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        """Advance with one closed bar. `held` is the side of the open position (BUY long, SELL
        short) as the strategy sees it at this bar's close; `can_afford` is whether one share fits
        the declared position value."""
        ...


class SignalScan(Protocol):
    name: str

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        """The round trips the rules take on `bars` (one instrument, oldest first)."""
        ...


@dataclass(frozen=True)
class ScanExecution:
    """The exchange and session assumptions a scan trades under. Defaults are the backtest's own
    (config/strategies/*.yaml, `FillSettings`)."""

    position_value: Decimal
    limit_buffer_bps: Decimal = Decimal(5)
    tick_size: Decimal = Decimal("0.05")
    participation: Decimal = Decimal("0.1")
    no_new_entries_after: time = time(14, 45)
    square_off_at: time = time(15, 15)
    forced_close_penalty_bps: Decimal = Decimal(10)
    screener_slippage_bps: Decimal = Decimal(5)  # what the screener charges per side on top

    def __post_init__(self) -> None:
        if self.position_value <= 0 or self.tick_size <= 0:
            raise ValueError("position value and tick size must be positive")
        if not Decimal(0) < self.participation <= Decimal(1):
            raise ValueError("participation must be in (0, 1]")
        if self.limit_buffer_bps < 0 or self.forced_close_penalty_bps < 0:
            raise ValueError("buffers and penalties cannot be negative")

    def limit_for(self, side: OrderSide, reference: Decimal) -> Decimal:
        """The marketable limit for a signal at `reference`: through the market, rounded to the
        tick in the direction that keeps it marketable (`domain.marketable`)."""
        buying = side is OrderSide.BUY
        moved = reference * ((1 + self.limit_buffer_bps / _BPS) if buying else
                             (1 - self.limit_buffer_bps / _BPS))  # fmt: skip
        mode = ROUND_CEILING if buying else ROUND_FLOOR
        return (moved / self.tick_size).to_integral_value(mode) * self.tick_size

    def forced_close_reference(self, held: OrderSide, last: Decimal) -> Decimal:
        """A reference price which, after the screener's own slippage, equals the broker's forced
        close: the last price moved `forced_close_penalty_bps` against the position (a long is
        sold lower, a short bought back higher)."""
        extra = (self.forced_close_penalty_bps - self.screener_slippage_bps) / _BPS
        return last * (1 - extra) if held is OrderSide.BUY else last * (1 + extra)


@dataclass
class _Order:
    side: OrderSide
    limit: Decimal
    quantity: int
    reference: Decimal  # the signal bar's close: what the screener prices the trade at
    placed_at: datetime  # the signal bar's close: only bars opening at or after it may fill this


@dataclass
class _Open:
    side: OrderSide
    quantity: int
    entry_reference: Decimal


class _DayBook:
    """One instrument's orders and position, as the exchange and the session hold them."""

    def __init__(self) -> None:
        self.entry: _Order | None = None
        self.exit: _Order | None = None
        self.position: _Open | None = None
        self.owned_by_session = False  # square-off has begun: the strategy's signals are refused
        self.last_close: Decimal | None = None


class IntradayScan:
    """Runs `ScanRules` through the backtest's order path for one instrument."""

    def __init__(self, name: str, rules: Callable[[], ScanRules], execution: ScanExecution) -> None:
        self.name = name
        self._rules = rules
        self._execution = execution

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        with localcontext(CONTEXT):
            return self._scan(instrument_id, bars)

    def _scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        rules = self._rules()
        trades: list[ScreenTrade] = []
        book = _DayBook()
        day: date | None = None
        for bar in bars:
            bar_day = bar.ts.astimezone(IST).date()
            if bar_day != day:
                if day is not None:
                    self._end_of_session(instrument_id, day, book, trades)
                day, book = bar_day, _DayBook()
            self._match(instrument_id, day, bar, book, trades)
            self._square_off_if_due(bar, book)
            self._strategy_step(rules, bar, book)
            book.last_close = bar.close.amount
        if day is not None:
            self._end_of_session(instrument_id, day, book, trades)
        return trades

    # --- the exchange lets the bar act on resting orders -------------------------------------
    def _match(
        self, instrument_id: str, day: date, bar: Candle, book: _DayBook, trades: list[ScreenTrade]
    ) -> None:
        if bar.partial:
            return
        capacity = int(bar.volume * self._execution.participation)
        # Orders trade in the order they were placed; an entry and an exit never coexist because
        # an exit is only signalled while a position is open, which needs the entry filled.
        for order in (book.entry, book.exit):
            if order is None or order.placed_at > bar.ts or order.quantity > capacity:
                continue
            if not self._crosses(order, bar):
                continue
            capacity -= order.quantity
            if order is book.entry:
                book.entry = None
                book.position = _Open(order.side, order.quantity, order.reference)
            else:
                book.exit = None
                self._close(instrument_id, day, book, order.reference, trades)

    @staticmethod
    def _crosses(order: _Order, bar: Candle) -> bool:
        """Strictly through, as `ThroughBar`: a buy needs low < limit, a sell high > limit."""
        if order.side is OrderSide.BUY:
            return bar.low.amount < order.limit
        return bar.high.amount > order.limit

    @staticmethod
    def _close(
        instrument_id: str, day: date, book: _DayBook, exit_reference: Decimal,
        trades: list[ScreenTrade],
    ) -> None:  # fmt: skip
        held = book.position
        assert held is not None
        trades.append(
            ScreenTrade(
                instrument_id,
                day,
                held.side,
                Money.of(held.entry_reference),
                Money.of(exit_reference),
            )
        )
        book.position = None

    # --- the session's own square-off --------------------------------------------------------
    def _square_off_if_due(self, bar: Candle, book: _DayBook) -> None:
        held = book.position
        if held is None or book.owned_by_session:
            return
        if bar.closes_at.astimezone(IST).time() < self._execution.square_off_at:
            return
        book.owned_by_session = True
        book.exit = self._order(_opposite(held.side), bar.close.amount, held.quantity, bar)

    # --- the strategy sees the bar -----------------------------------------------------------
    def _strategy_step(self, rules: ScanRules, bar: Candle, book: _DayBook) -> None:
        held_side = None if book.position is None else book.position.side
        can_afford = bar.close.amount <= self._execution.position_value
        intent = rules.observe(bar, live=not bar.partial, held=held_side, can_afford=can_afford)
        if intent is None or book.owned_by_session:
            return  # nothing to say, or the session refuses the strategy's signals now
        if isinstance(intent, EntryIntent):
            quantity = int(self._execution.position_value // bar.close.amount)
            if quantity >= 1 and book.position is None and book.entry is None:
                book.entry = self._order(intent.side, bar.close.amount, quantity, bar)
        elif book.position is not None and book.exit is None:
            book.exit = self._order(
                _opposite(book.position.side), bar.close.amount, book.position.quantity, bar
            )

    def _order(self, side: OrderSide, close: Decimal, quantity: int, bar: Candle) -> _Order:
        return _Order(side, self._execution.limit_for(side, close), quantity, close, bar.closes_at)

    # --- the exchange's end of day -----------------------------------------------------------
    def _end_of_session(
        self, instrument_id: str, day: date, book: _DayBook, trades: list[ScreenTrade]
    ) -> None:
        """Day orders expire, then the broker closes what is still open at the last price, moved
        against the position by the forced-close penalty."""
        if book.position is None or book.last_close is None:
            return
        held_side = book.position.side
        reference = self._execution.forced_close_reference(held_side, book.last_close)
        self._close(instrument_id, day, book, reference, trades)

"""The facts the risk engine judges a signal against, read from the platform's own books and market.

Each source answers one question and is built from the same objects the rest of the worker uses, so
risk sees exactly what the dashboard sees. Where a fact cannot be established the source says so
in the direction that BLOCKS: an unreadable quote is a stale instrument, an unknown price is no
price, an unhealthy venue is unhealthy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol

from emporos.broker.models import Quote
from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.domain.signals import Signal
from emporos.persistence.records import OrderRecord
from emporos.portfolio.service import PortfolioService
from emporos.risk.snapshot import AccountFacts, InstrumentMarket, OrderFlowFacts, WorkingOrder
from emporos.session.strategy_positions import StrategyPositionBook

_WORKING = ("PENDING_NEW", "OPEN", "PARTIALLY_FILLED", "PENDING_CANCEL", "UNKNOWN")


class MarkSource(Protocol):
    def marks(self) -> Mapping[str, Money]: ...


class QuoteSource(Protocol):
    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]: ...


class RecentOrders(Protocol):
    async def orders_created_since(self, since: datetime) -> list[OrderRecord]: ...


class WorkingOrders(Protocol):
    async def active(self) -> list[OrderRecord]: ...


class LedgerAccountFacts:
    def __init__(
        self, portfolio: PortfolioService, book: StrategyPositionBook, marks: MarkSource
    ) -> None:
        self._portfolio = portfolio
        self._book = book
        self._marks = marks

    async def account_facts(self, signal: Signal) -> AccountFacts:
        view = await self._portfolio.view()
        return AccountFacts(
            positions={
                p.instrument_id: Position(p.instrument_id, p.net_quantity, p.average_price)
                for p in view.positions
                if p.net_quantity
            },
            daily_pnl=view.valuation.realised + (view.valuation.unrealised or Money.zero()),
            strategy_pnl=self._book.pnl_by_run(self._marks.marks()),
        )


class QuotedMarketFacts:
    """Bid/ask and circuits from a fresh quote; freshness from the tick-driven marks."""

    def __init__(self, quotes: QuoteSource, marks: MarkSource) -> None:
        self._quotes = quotes
        self._marks = marks

    async def market_facts(self, instrument_id: str) -> InstrumentMarket:
        stale = instrument_id not in self._marks.marks()
        try:
            quotes = await self._quotes.get_quote([instrument_id])
        except Exception:
            return InstrumentMarket(stale=True)  # cannot read the market: treat it as stale
        quote = next((q for q in quotes if q.instrument_id == instrument_id), None)
        if quote is None:
            return InstrumentMarket(stale=True)
        return InstrumentMarket(
            ltp=quote.ltp,
            bid=quote.bid,
            ask=quote.ask,
            lower_circuit=quote.lower_circuit,
            upper_circuit=quote.upper_circuit,
            stale=stale,
        )


class JournalOrderFlow:
    def __init__(
        self,
        working: WorkingOrders,
        recent: RecentOrders,
        clock: Clock,
        window: timedelta = timedelta(minutes=1),
    ) -> None:
        self._working = working
        self._recent = recent
        self._clock = clock
        self._window = window

    async def order_flow(self) -> OrderFlowFacts:
        orders = [o for o in await self._working.active() if o.state in _WORKING]
        sent = await self._recent.orders_created_since(self._clock.now() - self._window)
        return OrderFlowFacts(
            working=tuple(
                WorkingOrder(o.instrument_id, OrderSide(o.side), o.created_at) for o in orders
            ),
            recent_order_times=tuple(o.created_at for o in sent),
        )


class VenueHealth:
    """Broker session and order-feed health, as the venue reports it. Unhealthy until it says so."""

    def __init__(self) -> None:
        self._session = False
        self._feed = False

    def set(self, *, session: bool, feed: bool) -> None:
        self._session, self._feed = session, feed

    def set_session(self, ok: bool) -> None:
        self._session = ok

    def set_feed(self, ok: bool) -> None:
        self._feed = ok

    def session_ok(self) -> bool:
        return self._session

    def order_feed_ok(self) -> bool:
        return self._feed

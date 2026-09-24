"""`AngelOneBroker`: the `Broker` implementation over Angel One (plan.md §4-5, EM-61).

Wraps Phase 4 (auth, REST), Phase 5 (market data) and Phase 6 (history); all translation to and
from SmartAPI lives in `mapping.py`. Every collaborator is a small Protocol defined here and
injected, so this class instantiates nothing and imports no concrete market-data class.

Important properties (each has a test):

* every failure is the classified `BrokerError` raised by the transport stack — an AMBIGUOUS one
  on `place_order` means "outcome unknown: resolve with `find_orders_by_tag`, never resend";
* `find_orders_by_tag` matches the tag EXACTLY, and refuses an empty tag (which could otherwise
  claim every untagged order placed in the broker's own app);
* `modify_order` exists because the interface has it, but repricing is cancel-then-replace: the
  execution engine must not use it to chase a price;
* ORDER PLACEMENT HAS NEVER BEEN EXERCISED LIVE: orders are accepted only from the API key's
  registered static IP (the production host's Elastic IP, 65.0.238.146), and none has been placed
  from it; dev machines cannot place one. Request shapes follow the docs and the SDK oracle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from emporos.broker.angelone.api import AngelOneApi, QuoteMode
from emporos.broker.angelone.mapping import (
    AccountMapper,
    InstrumentLocator,
    OrderRequestMapper,
)
from emporos.broker.angelone.session import Session
from emporos.broker.base import Broker
from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import (
    BrokerHolding,
    BrokerOrder,
    BrokerOrderAck,
    BrokerOrderUpdate,
    BrokerPosition,
    BrokerSession,
    BrokerTrade,
    CancelOrderRequest,
    CandleRequest,
    Funds,
    MarketDataMode,
    ModifyOrderRequest,
    PlaceOrderRequest,
    Profile,
    Quote,
)
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument, InstrumentResolver
from emporos.domain.ticks import Tick

_LOG = logging.getLogger(__name__)
_QUOTE_BATCH = 50
_M1_SPAN = timedelta(days=28)  # the broker silently truncates a 1m request beyond 30 days


class SessionService(Protocol):
    async def authenticate(self) -> Session: ...

    async def session(self) -> Session: ...

    async def logout(self) -> None: ...


class InstrumentCatalog(Protocol):
    async def load(self) -> Sequence[Instrument]: ...


class CandleFetcher(Protocol):
    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class MarketDataControl(Protocol):
    async def subscribe(self, instruments: Iterable[Instrument]) -> None: ...

    async def unsubscribe(self, instruments: Iterable[Instrument]) -> None: ...


class TickRegistry(Protocol):
    def add_handler(self, handler: Callable[[Tick], None]) -> None: ...


class OrderUpdateRegistry(Protocol):
    def add_handler(self, handler: Callable[[BrokerOrderUpdate], None]) -> None: ...


class AngelOneBroker(Broker):
    def __init__(
        self,
        api: AngelOneApi,
        sessions: SessionService,
        client_code: str,
        resolver: InstrumentResolver,
        catalog: InstrumentCatalog,
        candles: CandleFetcher,
        market_data: MarketDataControl,
        ticks: TickRegistry,
        order_updates: OrderUpdateRegistry,
        account: AccountMapper | None = None,
    ) -> None:
        self._api = api
        self._sessions = sessions
        self._client_code = client_code
        self._catalog = catalog
        self._candles = candles
        self._market_data = market_data
        self._ticks = ticks
        self._order_updates = order_updates
        self._account = account or AccountMapper()
        self._locator = InstrumentLocator(resolver)
        self._requests = OrderRequestMapper(self._locator)

    # --- session ---------------------------------------------------------------------------
    async def authenticate(self) -> BrokerSession:
        return self._neutral(await self._sessions.authenticate())

    async def ensure_session(self) -> BrokerSession:
        return self._neutral(await self._sessions.session())

    async def logout(self) -> None:
        await self._sessions.logout()

    async def get_profile(self) -> Profile:
        return self._account.profile(await self._api.profile())

    # --- reference data --------------------------------------------------------------------
    async def get_instruments(self) -> Sequence[Instrument]:
        return await self._catalog.load()

    # --- market data -----------------------------------------------------------------------
    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        instruments = [self._locator.locate(i) for i in instrument_ids]
        by_id: dict[str, Quote] = {}
        for exchange in {i.exchange for i in instruments}:
            tokens = [i.token for i in instruments if i.exchange is exchange]
            for start in range(0, len(tokens), _QUOTE_BATCH):
                batch = tokens[start : start + _QUOTE_BATCH]
                response = await self._api.quotes(QuoteMode.FULL, {exchange.value: batch})
                for entry in response.fetched:
                    quote = self._account.quote(entry)
                    by_id[quote.instrument_id] = quote
        return [by_id[i] for i in instrument_ids if i in by_id]  # request order; unknown omitted

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]:
        instrument = self._locator.locate(request.instrument_id)
        bars: dict[datetime, Candle] = {}
        for start, end in self._spans(request):
            for bar in await self._candles.fetch(instrument, request.timeframe, start, end):
                if request.start <= bar.ts < request.end:
                    bars[bar.ts] = bar
        return [bars[ts] for ts in sorted(bars)]

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None:
        # The feed runs in QUOTE mode, which carries everything LTP does: `mode` is a minimum.
        del mode
        await self._market_data.subscribe([self._locator.locate(i) for i in instrument_ids])

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None:
        await self._market_data.unsubscribe([self._locator.locate(i) for i in instrument_ids])

    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        self._ticks.add_handler(handler)

    # --- orders ----------------------------------------------------------------------------
    async def place_order(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        response = await self._api.place_order(self._requests.place(request))
        return BrokerOrderAck(response.order_id, request.client_tag)

    async def modify_order(self, request: ModifyOrderRequest) -> BrokerOrderAck:
        response = await self._api.modify_order(self._requests.modify(request))
        return BrokerOrderAck(response.order_id or request.broker_order_id)

    async def cancel_order(self, request: CancelOrderRequest) -> BrokerOrderAck:
        response = await self._api.cancel_order(self._requests.cancel(request))
        return BrokerOrderAck(response.order_id or request.broker_order_id)

    async def get_order_book(self) -> list[BrokerOrder]:
        orders: list[BrokerOrder] = []
        for entry in await self._api.order_book():
            try:
                orders.append(self._account.order(entry))
            except BrokerRejectedError:  # one unreadable row must not hide the rest of the book
                _LOG.warning("skipping an unreadable order-book row")
        return orders

    async def get_trade_book(self) -> list[BrokerTrade]:
        trades: list[BrokerTrade] = []
        for entry in await self._api.trade_book():
            try:
                trades.append(self._account.trade(entry))
            except BrokerRejectedError:
                _LOG.warning("skipping an unreadable trade-book row")
        return trades

    async def find_orders_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        if not client_tag:
            raise ValueError("a client tag is required: an empty one would match untagged orders")
        return [o for o in await self.get_order_book() if o.client_tag == client_tag]

    def on_order_update(self, handler: Callable[[BrokerOrderUpdate], None]) -> None:
        self._order_updates.add_handler(handler)

    # --- account ---------------------------------------------------------------------------
    async def get_positions(self) -> list[BrokerPosition]:
        return [self._account.position(e) for e in await self._api.positions()]

    async def get_holdings(self) -> list[BrokerHolding]:
        return [self._account.holding(e) for e in await self._api.holdings()]

    async def get_funds(self) -> Funds:
        return self._account.funds(await self._api.funds())

    # --- helpers ---------------------------------------------------------------------------
    def _neutral(self, session: Session) -> BrokerSession:
        return BrokerSession(
            client_code=self._client_code,
            established_at=session.established_at.astimezone(UTC),
            expires_at=session.expires_at.astimezone(UTC),
        )

    @staticmethod
    def _spans(request: CandleRequest) -> list[tuple[datetime, datetime]]:
        if request.timeframe is not Timeframe.M1 or request.end - request.start <= _M1_SPAN:
            return [(request.start, request.end)]
        spans, cursor = [], request.start
        while cursor < request.end:
            spans.append((cursor, min(cursor + _M1_SPAN, request.end)))
            cursor += _M1_SPAN
        return spans

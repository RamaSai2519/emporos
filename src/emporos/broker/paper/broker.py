"""`PaperBroker`: the `Broker` interface over real market data and a simulated exchange.

It is a façade over single-purpose collaborators — the market-data source, the simulated
exchange, the account and funds, the reject screen, the cost model and the journal — and owns
only the choreography between them. Paper and live run the SAME code paths above the `Broker`
interface (Decision 8): limit orders only, ordertag-based idempotency, ambiguous outcomes.

It never touches an order endpoint: the market-data source it is given has no order methods, and
it holds no credentials. Simulated orders live entirely in this process and in the journal.

Deliberate differences from the real broker, each on the safe side:
* a second order with a client tag already in use is REFUSED (a live broker would create a
  duplicate) so a resend bug fails loudly in paper instead of silently doubling a position;
* an order that would trade on placement waits for the next tick instead (see `exchange`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from emporos.broker.base import Broker
from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
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
from emporos.broker.paper.account import PaperAccount, PositionState
from emporos.broker.paper.costs import CostModel
from emporos.broker.paper.exchange import ExchangeEvent, SimulatedExchange
from emporos.broker.paper.fanout import Fanout
from emporos.broker.paper.faults import ReplyLoss
from emporos.broker.paper.funds import LastPrices, PaperFunds
from emporos.broker.paper.journal import (
    FillRecorded,
    OrderStateRecorded,
    PaperJournal,
    SnapshotRecorded,
)
from emporos.broker.paper.market import MarketDataSource
from emporos.broker.paper.rejects import OrderContext, OrderScreen, RejectionStage
from emporos.broker.paper.session import PaperSessionKeeper
from emporos.core.clock import IST, Clock
from emporos.domain.candles import Candle
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.ticks import Tick

_LOG = logging.getLogger(__name__)


class PaperBroker(Broker):
    def __init__(
        self,
        source: MarketDataSource,
        exchange: SimulatedExchange,
        account: PaperAccount,
        funds: PaperFunds,
        prices: LastPrices,
        screen: OrderScreen,
        costs: CostModel,
        journal: PaperJournal,
        replies: ReplyLoss,
        sessions: PaperSessionKeeper,
        clock: Clock,
    ) -> None:
        self._source = source
        self._exchange = exchange
        self._account = account
        self._funds = funds
        self._prices = prices
        self._screen = screen
        self._costs = costs
        self._journal = journal
        self._replies = replies
        self._sessions = sessions
        self._clock = clock
        self._instruments: dict[str, Instrument] | None = None
        self._subscribed: set[str] = set()
        self._ticks: Fanout[Tick] = Fanout("tick")
        self._updates: Fanout[BrokerOrderUpdate] = Fanout("order update")
        source.on_tick(self._on_source_tick)

    # --- session ---------------------------------------------------------------------------
    async def authenticate(self) -> BrokerSession:
        return self._sessions.login()

    async def ensure_session(self) -> BrokerSession:
        return self._sessions.current()

    async def logout(self) -> None:
        self._sessions.logout()

    async def get_profile(self) -> Profile:
        return Profile(self._sessions.client_code, (Exchange.NSE, Exchange.BSE))

    # --- reference and market data (real data, straight from the source) -------------------
    async def get_instruments(self) -> Sequence[Instrument]:
        return list((await self._catalog()).values())

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        await self._locate_all(instrument_ids)
        return await self._source.get_quote(instrument_ids)

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]:
        await self._locate(request.instrument_id)
        return await self._source.get_historical_candles(request)

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None:
        await self._locate_all(instrument_ids)
        await self._source.subscribe_market_data(instrument_ids, mode)
        self._subscribed.update(instrument_ids)

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None:
        await self._locate_all(instrument_ids)
        await self._source.unsubscribe_market_data(instrument_ids)
        self._subscribed.difference_update(instrument_ids)

    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        self._ticks.add(handler)

    # --- orders ----------------------------------------------------------------------------
    async def place_order(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        instrument = await self._locate(request.instrument_id)
        if self._exchange.find_by_tag(request.client_tag):
            raise BrokerRejectedError(
                f"client tag {request.client_tag!r} is already in use: an order is never resent"
            )
        if request.instrument_id not in self._subscribed:
            _LOG.warning(
                "order on %s but its ticks are not subscribed through this broker: it can only "
                "fill if something else subscribes them",
                request.instrument_id,
            )
        rejection = self._screen.first_rejection(self._context(request, instrument))
        if rejection is not None and rejection.stage is RejectionStage.AT_ACCEPTANCE:
            raise BrokerRejectedError(rejection.reason)
        event = self._exchange.submit(
            request, self._clock.now(), None if rejection is None else rejection.reason
        )
        self._apply([event])
        await self._make_durable()
        if self._replies.loses_reply(request):
            raise BrokerTransportError("simulated timeout: the reply to the order was lost")
        return BrokerOrderAck(event.order.broker_order_id, request.client_tag)

    async def modify_order(self, request: ModifyOrderRequest) -> BrokerOrderAck:
        existing = self._exchange.get(request.broker_order_id)
        proposed = PlaceOrderRequest(
            existing.instrument_id,
            existing.side,
            request.order_type,
            request.quantity,
            request.price,
            existing.client_tag or "MODIFY",
            trigger_price=request.trigger_price,
        )
        instrument = await self._locate(existing.instrument_id)
        rejection = self._screen.first_acceptance_rejection(self._context(proposed, instrument))
        if rejection is not None:
            raise BrokerRejectedError(rejection.reason)
        event = self._exchange.amend(
            request.broker_order_id,
            self._clock.now(),
            request.quantity,
            request.price,
            request.trigger_price,
        )
        self._apply([event])
        await self._make_durable()
        return BrokerOrderAck(request.broker_order_id, existing.client_tag)

    async def cancel_order(self, request: CancelOrderRequest) -> BrokerOrderAck:
        event = self._exchange.cancel(request.broker_order_id, self._clock.now())
        self._apply([event])
        await self._make_durable()
        return BrokerOrderAck(request.broker_order_id, event.order.client_tag)

    async def get_order_book(self) -> list[BrokerOrder]:
        return self._exchange.orders()

    async def get_trade_book(self) -> list[BrokerTrade]:
        return self._account.trades()

    async def find_orders_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        if not client_tag:
            raise ValueError("a client tag is required")
        return self._exchange.find_by_tag(client_tag)

    def on_order_update(self, handler: Callable[[BrokerOrderUpdate], None]) -> None:
        self._updates.add(handler)

    # --- account ---------------------------------------------------------------------------
    async def get_positions(self) -> list[BrokerPosition]:
        return [self._position(p) for p in self._account.positions()]

    async def get_holdings(self) -> list[BrokerHolding]:
        return []  # intraday only: nothing is ever held overnight

    async def get_funds(self) -> Funds:
        return self._funds.funds()

    # --- paper-specific --------------------------------------------------------------------
    async def flush_journal(self) -> None:
        """Make every fill so far durable. Call it on a cadence, at shutdown and before reading
        the store; placement and cancellation flush by themselves."""
        await self._journal.flush()

    def record_snapshot(self) -> None:
        """Queue a P&L snapshot (also taken automatically after every fill)."""
        self._journal.append(self._snapshot())

    # --- internals -------------------------------------------------------------------------
    def _on_source_tick(self, tick: Tick) -> None:
        """A fill applies before any downstream handler sees the tick that caused it."""
        self._prices.observe(tick)
        try:
            self._apply(self._exchange.on_tick(tick, self._clock.now()))
        except Exception:
            # A defect in the simulation must not silence the market data stream.
            _LOG.exception("paper fill simulation failed on a tick for %s", tick.instrument_id)
        self._ticks.publish(tick)

    def _apply(self, events: Sequence[ExchangeEvent]) -> None:
        for event in events:
            account_id, session_date = self._sessions.client_code, self._session_date()
            if event.trade is None:
                self._journal.append(
                    OrderStateRecorded(
                        account_id, session_date, event.order, event.seq, event.at, event.reason
                    )
                )
            else:
                fees = self._costs.charges(event.trade)
                position = self._account.apply(event.trade, fees)
                self._journal.append(
                    FillRecorded(
                        account_id,
                        session_date,
                        event.order,
                        event.seq,
                        event.at,
                        event.trade,
                        fees,
                        position,
                        len(self._account.trades()),
                    )
                )
                self._journal.append(self._snapshot())
            self._updates.publish(BrokerOrderUpdate(event.order, self._clock.now()))

    async def _make_durable(self) -> None:
        try:
            await self._journal.flush()
        except Exception as error:
            # The order exists in the simulation but its record may not: the caller cannot know,
            # exactly as with a lost reply, and must resolve it by tag — never resend.
            raise BrokerTransportError("the paper journal could not be written") from error

    def _snapshot(self) -> SnapshotRecorded:
        return SnapshotRecorded(
            self._sessions.client_code,
            self._session_date(),
            self._clock.now(),
            self._account.cash,
            self._account.realised,
            self._funds.unrealised(),
            self._account.fees,
            len(self._account.trades()),
            tuple(self._account.positions()),
        )

    def _session_date(self) -> str:
        return self._clock.now().astimezone(IST).date().isoformat()

    def _context(self, request: PlaceOrderRequest, instrument: Instrument) -> OrderContext:
        return OrderContext(
            request,
            instrument,
            self._prices.last(request.instrument_id),
            self._funds.available_cash(),
            self._funds.margin_required(request),
        )

    def _position(self, position: PositionState) -> BrokerPosition:
        return BrokerPosition(
            position.instrument_id,
            position.net_quantity,
            position.average_price,
            self._prices.last(position.instrument_id),
            position.realised,
            self._funds.unrealised_for(position.instrument_id),
        )

    async def _catalog(self) -> dict[str, Instrument]:
        if self._instruments is None:
            self._instruments = {i.instrument_id: i for i in await self._source.get_instruments()}
        return self._instruments

    async def _locate(self, instrument_id: str) -> Instrument:
        try:
            return (await self._catalog())[instrument_id]
        except KeyError:
            raise BrokerRejectedError(f"unknown instrument {instrument_id!r}") from None

    async def _locate_all(self, instrument_ids: Sequence[str]) -> None:
        for instrument_id in instrument_ids:
            await self._locate(instrument_id)

"""The `Broker` interface: the ONLY thing strategies, risk and execution may see of a broker
(plan.md §5). `AngelOneBroker`, `PaperBroker` and `SimulatedBroker` all implement it and all
pass the same contract suite (`tests/contract/`), so a strategy behaves identically in backtest,
paper and live.

Errors are the classified `BrokerError` family (`broker.errors`): an AMBIGUOUS failure on a
mutating call means "outcome unknown — resolve with `find_orders_by_tag`, never resend".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

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
from emporos.domain.candles import Candle
from emporos.domain.instruments import Instrument
from emporos.domain.ticks import Tick


class Broker(ABC):
    # --- session ---------------------------------------------------------------------------
    @abstractmethod
    async def authenticate(self) -> BrokerSession:
        """Log in afresh."""

    @abstractmethod
    async def ensure_session(self) -> BrokerSession:
        """Idempotent: return the live session, logging in again only if there is none or it
        has expired."""

    @abstractmethod
    async def logout(self) -> None: ...

    @abstractmethod
    async def get_profile(self) -> Profile: ...

    # --- reference data --------------------------------------------------------------------
    @abstractmethod
    async def get_instruments(self) -> Sequence[Instrument]: ...

    # --- market data -----------------------------------------------------------------------
    @abstractmethod
    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]: ...

    @abstractmethod
    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]: ...

    @abstractmethod
    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None: ...

    @abstractmethod
    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None: ...

    @abstractmethod
    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        """Register a handler for normalized ticks. Handlers run in order, synchronously."""

    # --- orders ----------------------------------------------------------------------------
    @abstractmethod
    async def place_order(self, request: PlaceOrderRequest) -> BrokerOrderAck: ...

    @abstractmethod
    async def modify_order(self, request: ModifyOrderRequest) -> BrokerOrderAck: ...

    @abstractmethod
    async def cancel_order(self, request: CancelOrderRequest) -> BrokerOrderAck: ...

    @abstractmethod
    async def get_order_book(self) -> list[BrokerOrder]: ...

    @abstractmethod
    async def get_trade_book(self) -> list[BrokerTrade]: ...

    @abstractmethod
    async def find_orders_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        """Orders whose client tag EXACTLY equals `client_tag` — the only safe way to resolve an
        order whose placement outcome is unknown (Decision 7)."""

    @abstractmethod
    def on_order_update(self, handler: Callable[[BrokerOrderUpdate], None]) -> None: ...

    # --- account ---------------------------------------------------------------------------
    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    async def get_holdings(self) -> list[BrokerHolding]: ...

    @abstractmethod
    async def get_funds(self) -> Funds: ...

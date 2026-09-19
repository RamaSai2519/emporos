"""The port through which a paper session is made durable.

Simulated orders, fills, positions and P&L go through the SAME storage real trading uses — paper
is not a lesser-tracked path. `PaperBroker` only knows this Protocol; the storage adapter lives
in `emporos.persistence` and implements it.

The journal is write-behind for fills, because a tick handler is synchronous and a fill must
apply before the next signal evaluates: `append` never blocks and preserves order, `flush` makes
everything appended so far durable. Placement and cancellation flush before they answer, so an
acknowledged order is always on disk. A flush that fails leaves the entries queued to be retried;
every write is idempotent (unique keys), so a retry cannot duplicate anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emporos.broker.models import BrokerOrder, BrokerTrade
from emporos.broker.paper.account import PositionState
from emporos.broker.paper.exchange import RestoredOrder
from emporos.domain.money import Money


@dataclass(frozen=True)
class OrderStateRecorded:
    """An order changed state; `seq` is the order's monotonic event sequence (1, 2, 3, ...)."""

    account_id: str
    session_date: str
    order: BrokerOrder
    seq: int
    at: datetime
    reason: str = ""


@dataclass(frozen=True)
class FillRecorded:
    """One fill: the trade, the order after it, and the position it produced."""

    account_id: str
    session_date: str
    order: BrokerOrder
    seq: int
    at: datetime
    trade: BrokerTrade
    fees: Money
    position: PositionState
    number: int  # 1-based position among the account's fills: the order to replay them in


@dataclass(frozen=True)
class SnapshotRecorded:
    """The account's P&L at a moment."""

    account_id: str
    session_date: str
    at: datetime
    cash: Money
    realised: Money
    unrealised: Money
    fees: Money
    trades: int  # fills booked so far: with `at`, identifies the snapshot
    positions: tuple[PositionState, ...]


JournalEntry = OrderStateRecorded | FillRecorded | SnapshotRecorded


class PaperJournal(Protocol):
    def append(self, entry: JournalEntry) -> None:
        """Queue an entry, in order, without blocking."""
        ...

    async def flush(self) -> None:
        """Return only once every entry appended so far is durable."""
        ...


@dataclass(frozen=True)
class RestoredFill:
    trade: BrokerTrade
    fees: Money


@dataclass(frozen=True)
class RestoredSession:
    """A session as persisted: enough to rebuild the exchange and the account exactly."""

    orders: Sequence[RestoredOrder]
    fills: Sequence[RestoredFill]


class PaperSessionStore(Protocol):
    async def load(self, account_id: str, session_date: str) -> RestoredSession: ...


class NullJournal:
    """Persists nothing. For runs whose result is read from memory (a sandboxed what-if), never
    for a session that must survive a restart."""

    def append(self, entry: JournalEntry) -> None:
        return None

    async def flush(self) -> None:
        return None

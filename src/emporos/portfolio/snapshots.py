"""Point-in-time portfolio snapshots: periodic during the session, and one at the close.

A snapshot is a record of what the worker computed at that moment — positions, P&L, cash — for the
dashboard's history and for audit. `SnapshotVerifier` proves one after the fact by rebuilding its
positions from the fill history alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Protocol

from emporos.core.clock import IST, Clock
from emporos.domain.money import Money
from emporos.persistence.records import (
    ExecutionRecord,
    PortfolioSnapshotRecord,
    PositionSnapshot,
)
from emporos.portfolio.replay import PositionReplay
from emporos.portfolio.service import PortfolioService


class SnapshotKind(StrEnum):
    INTRADAY = "INTRADAY"
    EOD = "EOD"


@dataclass(frozen=True)
class SnapshotSchedule:
    """How often to snapshot during the session, and when the end-of-day one is due."""

    interval: timedelta = timedelta(minutes=5)
    eod_at: time = time(15, 35)  # IST wall clock, after the 15:30 close

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValueError("the snapshot interval must be positive")

    def due(
        self, now: datetime, last_intraday: datetime | None, eod_taken: bool
    ) -> SnapshotKind | None:
        local = now.astimezone(IST)
        if not eod_taken and local.time() >= self.eod_at:
            return SnapshotKind.EOD
        if local.time() < self.eod_at and (
            last_intraday is None or now - last_intraday >= self.interval
        ):
            return SnapshotKind.INTRADAY
        return None


class CashSource(Protocol):
    async def available_cash(self) -> Money | None: ...


class FillCounter(Protocol):
    async def fills_this_session(self, session_date: str) -> int: ...


class SnapshotSink(Protocol):
    async def insert(self, record: PortfolioSnapshotRecord) -> None: ...


class SnapshotService:
    def __init__(
        self,
        account_id: str,
        portfolio: PortfolioService,
        cash: CashSource,
        fills: FillCounter,
        sink: SnapshotSink,
        clock: Clock,
    ) -> None:
        self._account_id = account_id
        self._portfolio = portfolio
        self._cash = cash
        self._fills = fills
        self._sink = sink
        self._clock = clock

    async def take(self, kind: SnapshotKind) -> PortfolioSnapshotRecord:
        now = self._clock.now()
        session = now.astimezone(IST).date().isoformat()
        view = await self._portfolio.view()
        record = PortfolioSnapshotRecord(
            _id=f"{self._account_id}:{kind.value}:{now.isoformat()}",
            account_id=self._account_id,
            ts=now,
            session_date=session,
            kind=kind.value,
            cash=await self._cash.available_cash(),
            realised_pnl=view.valuation.realised,
            unrealised_pnl=view.valuation.unrealised,
            fees=view.valuation.fees,
            trades=await self._fills.fills_this_session(session),
            positions=[
                PositionSnapshot(
                    instrument_id=p.instrument_id,
                    net_quantity=p.net_quantity,
                    average_price=p.average_price,
                    realised_pnl=p.realised_pnl,
                    fees=p.fees,
                )
                for p in view.positions
            ],
        )
        await self._sink.insert(record)
        return record


class SnapshotVerifier:
    """Does a snapshot's account state follow from the fills that had happened by then?"""

    def __init__(self, replay: PositionReplay) -> None:
        self._replay = replay

    def problems(
        self, snapshot: PortfolioSnapshotRecord, executions: list[ExecutionRecord]
    ) -> list[str]:
        known = [e for e in executions if e.ts <= snapshot.ts]
        replayed = self._replay.replay(snapshot.account_id, known)
        held = {p.instrument_id: p for p in snapshot.positions}
        problems: list[str] = []
        for instrument in sorted(held.keys() | replayed.keys()):
            ours, theirs = held.get(instrument), replayed.get(instrument)
            if ours is None or theirs is None:
                problems.append(f"{instrument}: not in both the snapshot and the fill history")
            elif (ours.net_quantity, ours.average_price, ours.realised_pnl, ours.fees) != (
                theirs.net_quantity,
                theirs.average_price,
                theirs.realised_pnl,
                theirs.fees or Money.zero(),
            ):
                problems.append(f"{instrument}: snapshot disagrees with the fills")
        return problems

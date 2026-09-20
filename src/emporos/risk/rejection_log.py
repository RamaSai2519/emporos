"""Persists every risk rejection to `risk_events` with its full evaluation context."""

from __future__ import annotations

from typing import Protocol

from emporos.core.ids import IdGenerator
from emporos.persistence.records import RiskEventRecord
from emporos.risk.approval import RiskRejection


class RiskEventStore(Protocol):
    """What the log needs from `RiskEventRepository`."""

    async def insert(self, record: RiskEventRecord) -> None: ...


class MongoRejectionLog:
    def __init__(self, store: RiskEventStore, ids: IdGenerator) -> None:
        self._store = store
        self._ids = ids

    async def record(self, rejection: RiskRejection) -> None:
        signal = rejection.signal
        await self._store.insert(
            RiskEventRecord(
                _id=self._ids.new_ulid(),
                rule=rejection.rule,
                ts=rejection.rejected_at,
                signal_id=rejection.signal_id,
                strategy_run_id=signal.strategy_run_id,
                instrument_id=signal.instrument_id,
                side=signal.side,
                kind=signal.kind.value,
                quantity=signal.quantity,
                limit_price=signal.limit_price,
                reason=rejection.reason,
                details=dict(rejection.details),
                trace=[
                    {"rule": t.rule, "allowed": t.allowed, "reason": t.reason}
                    for t in rejection.trace
                ],
                snapshot=dict(rejection.snapshot),
            )
        )

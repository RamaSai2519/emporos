"""Persists every signal a strategy emits, before anything acts on it (plan.md §11: a silently
dropped signal is unauditable).

`SignalRecorder` is a `SignalSink`. It stamps each record with a per-run sequence so signals with
the same timestamp keep their order. Linking a signal to the order it produced (`ordertag`) is the
execution engine's job in Phase 12; this records intent only.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from emporos.core.ids import IdGenerator
from emporos.domain.signals import Signal
from emporos.persistence.records import SignalRecord


class SignalStore(Protocol):
    """What the recorder needs from `SignalRepository`."""

    async def insert(self, record: SignalRecord) -> None: ...


class SignalRecorder:
    def __init__(self, store: SignalStore, ids: IdGenerator) -> None:
        self._store = store
        self._ids = ids
        self._sequence: defaultdict[str, int] = defaultdict(int)

    async def submit(self, signal: Signal) -> None:
        self._sequence[signal.strategy_run_id] += 1
        await self._store.insert(
            SignalRecord(
                _id=self._ids.new_ulid(),
                strategy_run_id=signal.strategy_run_id,
                instrument_id=signal.instrument_id,
                ts=signal.ts,
                kind=signal.kind.value,
                side=signal.side,
                order_type=signal.order_type,
                price=signal.limit_price,
                trigger_price=signal.trigger_price,
                quantity=signal.quantity,
                reason=signal.reason,
                sequence=self._sequence[signal.strategy_run_id],
            )
        )

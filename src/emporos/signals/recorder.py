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
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import SignalRecord


class SignalStore(Protocol):
    """What the recorder needs from `SignalRepository`."""

    async def insert(self, record: SignalRecord) -> None: ...

    async def get(self, record_id: str) -> SignalRecord | None: ...

    async def replace(self, record: SignalRecord) -> None: ...


class SignalRecorder:
    def __init__(self, store: SignalStore, ids: IdGenerator) -> None:
        self._store = store
        self._ids = ids
        self._sequence: defaultdict[str, int] = defaultdict(int)

    async def submit(self, signal: Signal) -> None:
        await self.record(signal)

    async def record(self, signal: Signal, signal_id: str | None = None) -> str:
        """Persist the signal and return its id: the key risk, execution and the order all carry.

        A caller-chosen `signal_id` makes recording idempotent: the second time, the signal is
        already on record and is not written again.
        """
        self._sequence[signal.strategy_run_id] += 1
        signal_id = signal_id or self._ids.new_ulid()
        try:
            await self._insert(signal, signal_id)
        except DuplicateRecordError:
            if await self._store.get(signal_id) is None:
                raise
        return signal_id

    async def _insert(self, signal: Signal, signal_id: str) -> None:
        await self._store.insert(
            SignalRecord(
                _id=signal_id,
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

    async def link(self, signal_id: str, ordertag: str) -> None:
        """Tie a signal to the order it produced (EM-99 H3)."""
        record = await self._store.get(signal_id)
        if record is not None:
            await self._store.replace(record.model_copy(update={"ordertag": ordertag}))

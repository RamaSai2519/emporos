"""Latency of one paper signal, read off the records the execution layer already wrote.

    market event ─▶ order created ─▶ broker acknowledged ─▶ first fill
       (decision)      (placement)          (fill)

Nothing new is captured on the paper side for this: `OrderRecord.created_at`, the `order_events`
trail and `ExecutionRecord.ts` already say it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from emporos.parity.models import LatencyProfile
from emporos.persistence.records import ExecutionRecord, OrderEventRecord, OrderRecord

_NOT_ACKED = frozenset({"PENDING_NEW"})


class LatencyProfiler:
    def profile(
        self,
        signal_ts: datetime,
        orders: Sequence[OrderRecord],
        events: Mapping[str, Sequence[OrderEventRecord]],
        executions: Sequence[ExecutionRecord],
    ) -> LatencyProfile | None:
        """None when the signal never produced an order (nothing to time)."""
        if not orders:
            return None
        first = min(orders, key=lambda o: o.created_at)
        acked = [e.ts for e in events.get(first.id, ()) if e.state not in _NOT_ACKED]
        ack = min(acked) if acked else None
        first_fill = min((x.ts for x in executions), default=None)
        return LatencyProfile(
            decision=first.created_at - signal_ts,
            placement=None if ack is None else ack - first.created_at,
            fill=None if ack is None or first_fill is None else self._span(ack, first_fill),
            reprices=len(orders) - 1,
        )

    @staticmethod
    def _span(start: datetime, end: datetime) -> timedelta:
        return end - start

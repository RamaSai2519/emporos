"""What the paper side of a comparison is made of: one strategy run's persisted records.

`PaperRunSource` is the only seam to storage; the Mongo implementation lives in `persistence` and
is wired at the composition root, so everything in this package tests against plain values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    RiskEventRecord,
    SignalRecord,
    StrategyRunRecord,
)


@dataclass(frozen=True)
class PaperRunData:
    run: StrategyRunRecord
    signals: tuple[SignalRecord, ...] = ()
    orders: tuple[OrderRecord, ...] = ()
    order_events: tuple[OrderEventRecord, ...] = ()
    executions: tuple[ExecutionRecord, ...] = ()
    risk_events: tuple[RiskEventRecord, ...] = field(default_factory=tuple)


class PaperRunSource(Protocol):
    async def runs_on(self, session_date: str) -> tuple[StrategyRunRecord, ...]:
        """Every strategy run recorded for one session day."""
        ...

    async def load(self, run: StrategyRunRecord) -> PaperRunData: ...

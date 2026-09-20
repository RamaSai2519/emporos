"""The one route a strategy's signal takes to the market: record → risk → execution → link.

    signal ──▶ SignalRecorder (persisted FIRST) ──▶ RiskEngine ──▶ ExecutionEngine ──▶ broker
                                                       │                │
                                       rejection persisted by risk     order carries signal_id

Every signal is on record before anything acts on it; a rejection is on record with its reason (risk
writes it); an execution refusal is alerted. A signal never reaches a broker except through both
gates, and the API and dashboard use this same sink for manual and square-off orders.
"""

from __future__ import annotations

from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.domain.signals import Signal
from emporos.execution.errors import ExecutionRefusedError
from emporos.persistence.records import OrderRecord
from emporos.risk.approval import RiskApprovedSignal, RiskDecision, RiskRejection


class SignalLedger(Protocol):
    async def record(self, signal: Signal) -> str: ...

    async def link(self, signal_id: str, ordertag: str) -> None: ...


class SignalReviewer(Protocol):
    async def review(self, signal: Signal, signal_id: str) -> RiskDecision: ...


class ApprovedOrders(Protocol):
    async def place(self, approval: RiskApprovedSignal) -> OrderRecord: ...


class GatedExecutionSink:
    """A `SignalSink`."""

    def __init__(
        self,
        ledger: SignalLedger,
        risk: SignalReviewer,
        execution: ApprovedOrders,
        alerts: AlertSink,
    ) -> None:
        self._ledger = ledger
        self._risk = risk
        self._execution = execution
        self._alerts = alerts

    async def submit(self, signal: Signal) -> None:
        signal_id = await self._ledger.record(signal)
        decision = await self._risk.review(signal, signal_id)
        if isinstance(decision, RiskRejection):
            return  # already persisted, with the rule and the state it judged
        try:
            order = await self._execution.place(decision)
        except ExecutionRefusedError as refused:
            self._alerts.raise_alert(
                "execution_refused", f"{signal.instrument_id} {signal.side.value}: {refused}"
            )
            return
        await self._ledger.link(signal_id, order.ordertag)

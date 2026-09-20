"""Compare immutable account snapshots without guessing away discrepancies.

The broker is the source of truth (plan.md §13). The comparator finds what differs; the reconciler
decides what to do about it, and the rule is deliberately narrow: the ONLY automatic repair is
adopting fills the broker reports and we have not applied yet, and only when every discrepancy found
is of a kind that adoption can explain. Anything structural — an order we do not know, a position on
one side only, a fill that disagrees — is never guessed at: trading halts for a human decision.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerPosition, BrokerTrade
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.persistence.records import (
    ExecutionRecord,
    OrderRecord,
    PositionRecord,
    ReconciliationRunRecord,
)
from emporos.portfolio.replay import PositionReplay, orders_disagreeing_with_fills
from emporos.risk.snapshot import ReconciliationStatus


class DiscrepancyKind(StrEnum):
    MISSING_BROKER_ORDER = "MISSING_BROKER_ORDER"
    EXTERNAL_ORDER = "EXTERNAL_ORDER"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    MISSING_FILL = "MISSING_FILL"
    FILL_MISMATCH = "FILL_MISMATCH"
    EXTERNAL_FILL = "EXTERNAL_FILL"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    ORPHAN_POSITION = "ORPHAN_POSITION"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"
    # Internal consistency: our own rows contradict our own fill history (no broker involved).
    POSITION_HISTORY_MISMATCH = "POSITION_HISTORY_MISMATCH"
    ORDER_FILL_MISMATCH = "ORDER_FILL_MISMATCH"


class ReconciliationTrigger(StrEnum):
    STARTUP = "STARTUP"
    PERIODIC = "PERIODIC"
    EOD = "EOD"
    MANUAL = "MANUAL"


_IN_FLIGHT = frozenset({"PENDING_NEW", "UNKNOWN", "PENDING_CANCEL"})
_NEVER_REACHED = frozenset({"PENDING_NEW", "UNKNOWN", "REJECTED"})

# What fill adoption can explain: the broker has fills we have not applied, so our order rows,
# our position and our executions all lag it. Nothing else is ever repaired automatically.
ADOPTABLE = frozenset(
    {
        DiscrepancyKind.EXTERNAL_FILL,
        DiscrepancyKind.ORDER_MISMATCH,
        DiscrepancyKind.POSITION_MISMATCH,
    }
)


@dataclass(frozen=True)
class Discrepancy:
    kind: DiscrepancyKind
    reference: str
    detail: str
    instrument_id: str = ""
    broker_only: bool = False  # for ORPHAN_POSITION: the position exists only at the broker


def adoptable(found: tuple[Discrepancy, ...]) -> bool:
    """True only if fill adoption could explain EVERY discrepancy. One that it cannot — an order we
    do not know, a fill that disagrees, a position we hold and the broker does not — makes the
    whole run ambiguous, and an ambiguous run adopts nothing."""
    unapplied = {d.instrument_id for d in found if d.kind is DiscrepancyKind.EXTERNAL_FILL}
    for d in found:
        if d.kind in ADOPTABLE:
            continue
        # A position that exists only at the broker is just the fills we have not applied yet.
        if (
            d.kind is DiscrepancyKind.ORPHAN_POSITION
            and d.broker_only
            and d.instrument_id in unapplied
        ):
            continue
        return False
    return True


@dataclass(frozen=True)
class ReconciliationSnapshot:
    orders: tuple[OrderRecord, ...]
    executions: tuple[ExecutionRecord, ...]
    positions: tuple[PositionRecord, ...]
    broker_orders: tuple[BrokerOrder, ...]
    broker_trades: tuple[BrokerTrade, ...]
    broker_positions: tuple[BrokerPosition, ...]
    # Every fill the account ever had, when known: lets our own rows be checked against it.
    history: tuple[ExecutionRecord, ...] | None = None


class ReconciliationComparator:
    """Detect missing, external and conflicting account facts; no automatic mutations."""

    def __init__(self, replay: PositionReplay | None = None) -> None:
        self._replay = replay

    def compare(self, snapshot: ReconciliationSnapshot) -> tuple[Discrepancy, ...]:
        found: list[Discrepancy] = []
        self._orders(snapshot, found)
        self._fills(snapshot, found)
        self._positions(snapshot, found)
        self._history(snapshot, found)
        return tuple(found)

    def _history(self, snapshot: ReconciliationSnapshot, found: list[Discrepancy]) -> None:
        if snapshot.history is None or self._replay is None:
            return
        account = snapshot.positions[0].account_id if snapshot.positions else ""
        replayed = self._replay.replay(account, snapshot.history)
        for instrument, detail in self._replay.differences(snapshot.positions, replayed):
            found.append(Discrepancy(DiscrepancyKind.POSITION_HISTORY_MISMATCH, instrument, detail))
        filled = self._replay.filled_by_order(snapshot.history)
        for order_id, detail in orders_disagreeing_with_fills(snapshot.orders, filled):
            found.append(Discrepancy(DiscrepancyKind.ORDER_FILL_MISMATCH, order_id, detail))

    def _orders(self, snapshot: ReconciliationSnapshot, found: list[Discrepancy]) -> None:
        broker = {o.broker_order_id: o for o in snapshot.broker_orders}
        if len(broker) != len(snapshot.broker_orders):
            found.append(
                Discrepancy(
                    DiscrepancyKind.DUPLICATE_IDENTITY, "orders", "duplicate broker order id"
                )
            )
        by_tag = {o.client_tag: o for o in snapshot.broker_orders if o.client_tag}
        known: set[str] = set()
        for order in snapshot.orders:
            # An order is ours if the broker holds it under our tag — even one we never learned
            # the broker's id for (a rejection, an unresolved placement).
            counterpart = by_tag.get(order.ordertag) or broker.get(order.broker_order_id or "")
            if counterpart is None:
                if not self._expected_absent(order):
                    found.append(
                        Discrepancy(
                            DiscrepancyKind.MISSING_BROKER_ORDER,
                            order.id,
                            "persisted order has no broker counterpart",
                        )
                    )
                continue
            known.add(counterpart.broker_order_id)
            if self._differs(order, counterpart):
                found.append(
                    Discrepancy(
                        DiscrepancyKind.ORDER_MISMATCH,
                        order.id,
                        "broker order differs from persisted intent or fills",
                    )
                )
        for order_id in broker.keys() - known:
            found.append(
                Discrepancy(
                    DiscrepancyKind.EXTERNAL_ORDER,
                    order_id,
                    "broker order was not placed by the execution engine",
                )
            )

    @staticmethod
    def _expected_absent(order: OrderRecord) -> bool:
        """An order the broker never heard of is normal while it is still being placed (its fate
        belongs to execution's own resolution) and after a definitive rejection."""
        return order.broker_order_id is None and order.state in _NEVER_REACHED

    @staticmethod
    def _differs(order: OrderRecord, counterpart: BrokerOrder) -> bool:
        expected_state = {
            BrokerOrderStatus.PENDING: "OPEN",
            BrokerOrderStatus.TRIGGER_PENDING: "OPEN",
        }.get(counterpart.status, counterpart.status.value)
        state_agrees = order.state in _IN_FLIGHT or order.state == expected_state
        return (
            order.instrument_id,
            order.side,
            order.quantity,
            order.limit_price,
            order.filled_quantity,
            order.ordertag,
            order.order_type,
            order.trigger_price,
            state_agrees,
            order.broker_order_id in (None, counterpart.broker_order_id),
        ) != (
            counterpart.instrument_id,
            counterpart.side,
            counterpart.quantity,
            counterpart.price,
            counterpart.filled_quantity,
            counterpart.client_tag,
            counterpart.order_type,
            counterpart.trigger_price,
            True,
            True,
        )

    def _fills(self, snapshot: ReconciliationSnapshot, found: list[Discrepancy]) -> None:
        broker = {t.trade_id: t for t in snapshot.broker_trades}
        internal = {t.broker_trade_id: t for t in snapshot.executions}
        if len(broker) != len(snapshot.broker_trades) or len(internal) != len(snapshot.executions):
            found.append(
                Discrepancy(DiscrepancyKind.DUPLICATE_IDENTITY, "fills", "duplicate trade id")
            )
        orders = {o.id: o.broker_order_id for o in snapshot.orders}
        for trade_id, fill in internal.items():
            counterpart = broker.get(trade_id)
            if counterpart is None:
                found.append(
                    Discrepancy(
                        DiscrepancyKind.MISSING_FILL,
                        trade_id,
                        "persisted fill is absent from broker trade book",
                    )
                )
            elif (
                fill.instrument_id,
                fill.side,
                fill.quantity,
                fill.price,
                orders.get(fill.order_id),
            ) != (
                counterpart.instrument_id,
                counterpart.side,
                counterpart.quantity,
                counterpart.price,
                counterpart.broker_order_id,
            ):
                found.append(
                    Discrepancy(DiscrepancyKind.FILL_MISMATCH, trade_id, "fill details differ")
                )
        for trade_id in broker.keys() - internal.keys():
            found.append(
                Discrepancy(
                    DiscrepancyKind.EXTERNAL_FILL,
                    trade_id,
                    "broker fill has not been applied locally",
                    instrument_id=broker[trade_id].instrument_id,
                )
            )

    def _positions(self, snapshot: ReconciliationSnapshot, found: list[Discrepancy]) -> None:
        internal = {p.instrument_id: p for p in snapshot.positions if p.net_quantity}
        broker = {p.instrument_id: p for p in snapshot.broker_positions if p.net_quantity}
        for instrument in internal.keys() | broker.keys():
            ours, theirs = internal.get(instrument), broker.get(instrument)
            if ours is None or theirs is None:
                found.append(
                    Discrepancy(
                        DiscrepancyKind.ORPHAN_POSITION,
                        instrument,
                        "position exists on only one side",
                        instrument_id=instrument,
                        broker_only=ours is None,
                    )
                )
            elif (ours.net_quantity, ours.average_price) != (
                theirs.net_quantity,
                theirs.average_price,
            ):
                found.append(
                    Discrepancy(
                        DiscrepancyKind.POSITION_MISMATCH,
                        instrument,
                        "position quantity or cost basis differs",
                    )
                )


class ReconciliationSource(Protocol):
    async def snapshot(self) -> ReconciliationSnapshot: ...


class ReconciliationLog(Protocol):
    async def record(self, record: ReconciliationRunRecord) -> None: ...


class TradingHalt(Protocol):
    async def halt(self, reason: str) -> None: ...


class FillAdopter(Protocol):
    """Applies the fills the broker reports that we have not applied (idempotent)."""

    async def adopt(self) -> None: ...


class ReconciliationTracker:
    """What the risk engine reads: is the account known to agree with the broker, right now?

    Fail-safe by construction: PENDING until a run has come back clean, FAILED after one that did
    not, and a clean result that has gone stale reads as PENDING again — a reconciler that stopped
    running must block trading exactly as one that failed does.
    """

    def __init__(self, clock: Clock, max_age: timedelta = timedelta(minutes=15)) -> None:
        if max_age <= timedelta(0):
            raise ValueError("a clean reconciliation must be allowed to live for a positive time")
        self._clock = clock
        self._max_age = max_age
        self._failed = False
        self._clean_at: datetime | None = None

    def clean(self) -> None:
        self._failed, self._clean_at = False, self._clock.now()

    def failed(self) -> None:
        self._failed, self._clean_at = True, None

    def status(self) -> ReconciliationStatus:
        if self._failed:
            return ReconciliationStatus.FAILED
        if self._clean_at is None or self._clock.now() - self._clean_at > self._max_age:
            return ReconciliationStatus.PENDING
        return ReconciliationStatus.CLEAN


class Reconciler:
    """Persist every comparison; adopt what is safely adoptable; halt on everything else."""

    def __init__(
        self,
        source: ReconciliationSource,
        comparator: ReconciliationComparator,
        adopter: FillAdopter,
        log: ReconciliationLog,
        halt: TradingHalt,
        tracker: ReconciliationTracker,
        clock: Clock,
        ids: IdGenerator,
        alerts: AlertSink,
    ) -> None:
        self._source, self._comparator, self._adopter = source, comparator, adopter
        self._log, self._halt, self._tracker = log, halt, tracker
        self._clock, self._ids, self._alerts = clock, ids, alerts

    async def run(
        self, trigger: ReconciliationTrigger = ReconciliationTrigger.PERIODIC
    ) -> tuple[Discrepancy, ...]:
        try:
            found = self._comparator.compare(await self._source.snapshot())
            healed: tuple[Discrepancy, ...] = ()
            if found and adoptable(found):
                await self._adopter.adopt()
                healed = found
                found = self._comparator.compare(await self._source.snapshot())
            await self._log.record(self._record(trigger, found, healed))
        except Exception:
            self._tracker.failed()
            await self._halt.halt("reconciliation could not establish account state")
            self._alerts.raise_alert("reconciliation_failed", "account state unavailable")
            raise
        if found:
            self._tracker.failed()
            await self._halt.halt(f"reconciliation found {len(found)} discrepancies")
            self._alerts.raise_alert("reconciliation_mismatch", f"{len(found)} discrepancies")
        else:
            self._tracker.clean()
        return found

    def _record(
        self,
        trigger: ReconciliationTrigger,
        found: tuple[Discrepancy, ...],
        healed: tuple[Discrepancy, ...],
    ) -> ReconciliationRunRecord:
        return ReconciliationRunRecord.model_validate(
            {
                "_id": self._ids.new_ulid(),
                "ts": self._clock.now(),
                "trigger": trigger.value,
                "status": "FAILED" if found else ("HEALED" if healed else "CLEAN"),
                "discrepancies": [_dump(d) for d in found],
                "healed": [_dump(d) for d in healed],
            }
        )


def _dump(d: Discrepancy) -> dict[str, str]:
    return {"kind": d.kind.value, "reference": d.reference, "detail": d.detail}

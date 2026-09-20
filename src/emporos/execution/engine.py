"""Risk-approved placement, cancellation and conservative restart recovery.

Placement protocol (plan.md §12): the intent is written FIRST (`PENDING_NEW`, unique on the
idempotency key), then one broker call is made, and an ambiguous outcome is never retried — it is
resolved by asking the broker what it holds under the order's tag. Every state change goes through
the journal together with its audit event.

Fills are not applied here: an order's `filled_quantity` moves only when a fill is processed
(`execution.fills`), so the executions are the single source of truth for quantities.
"""

import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

from emporos.broker.errors import BrokerError
from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    CancelOrderRequest,
    PlaceOrderRequest,
)
from emporos.broker.ratelimit import RateLimiter
from emporos.core.clock import Clock
from emporos.core.errors import ErrorClassification
from emporos.core.ids import IdGenerator
from emporos.execution.errors import (
    ApprovalExpiredError,
    InstrumentFrozenError,
    InvalidReplacementError,
)
from emporos.execution.ports import OrderGateway, OrderJournal, OrderPricer
from emporos.execution.resolution import AbsencePolicy
from emporos.execution.state import OrderState, OrderStateMachine
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import OrderRecord
from emporos.risk.approval import RiskApprovedSignal

_IST = ZoneInfo("Asia/Kolkata")
_BROKER_STATE = {
    BrokerOrderStatus.PENDING: OrderState.OPEN,
    BrokerOrderStatus.OPEN: OrderState.OPEN,
    BrokerOrderStatus.TRIGGER_PENDING: OrderState.OPEN,
    BrokerOrderStatus.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
    BrokerOrderStatus.FILLED: OrderState.FILLED,
    BrokerOrderStatus.CANCELLED: OrderState.CANCELLED,
    BrokerOrderStatus.REJECTED: OrderState.REJECTED,
    BrokerOrderStatus.UNRECOGNISED: OrderState.UNKNOWN,
}
_UNRESOLVED = (OrderState.UNKNOWN, OrderState.PENDING_NEW, OrderState.PENDING_CANCEL)


class ExecutionEngine:
    """Own the single serialized path from a risk approval to a durable broker intent."""

    def __init__(
        self,
        gateway: OrderGateway,
        journal: OrderJournal,
        limiter: RateLimiter,
        clock: Clock,
        ids: IdGenerator,
        machine: OrderStateMachine,
        pricer: OrderPricer,
        account_id: str,
        absence: AbsencePolicy | None = None,
        approval_lifetime: timedelta = timedelta(seconds=5),
    ) -> None:
        if not account_id or approval_lifetime <= timedelta(0):
            raise ValueError("an account and positive approval lifetime are required")
        self._gateway = gateway
        self._journal = journal
        self._limiter = limiter
        self._clock = clock
        self._ids = ids
        self._machine = machine
        self._pricer = pricer
        self._account_id = account_id
        self._absence = absence or AbsencePolicy()
        self._approval_lifetime = approval_lifetime
        self._lock = asyncio.Lock()

    async def place(
        self,
        approval: RiskApprovedSignal,
        *,
        parent_order_id: str | None = None,
        marketable: bool = True,
    ) -> OrderRecord:
        if not isinstance(approval, RiskApprovedSignal):
            raise TypeError("execution accepts only RiskApprovedSignal")
        async with self._lock:
            key = f"{self._account_id}:{approval.signal_id}"
            existing = await self._journal.by_key(key)
            if existing is not None:
                return existing
            signal = approval.signal
            parent = await self._parent(parent_order_id, approval)
            if await self._journal.unresolved(signal.instrument_id):
                raise InstrumentFrozenError("instrument frozen by an unresolved order")
            now = self._clock.now()
            if not timedelta(0) <= now - approval.approved_at <= self._approval_lifetime:
                raise ApprovalExpiredError("risk approval expired; review against a fresh snapshot")
            limit, trigger = await self._pricer.price(approval, marketable=marketable)
            order_id = self._ids.new_ulid()
            order = OrderRecord(
                _id=order_id,
                idempotency_key=key,
                ordertag=order_id[-20:],
                instrument_id=signal.instrument_id,
                side=signal.side,
                order_type=signal.order_type,
                quantity=signal.quantity,
                limit_price=limit,
                trigger_price=trigger,
                state=OrderState.PENDING_NEW,
                account_id=self._account_id,
                session_date=now.astimezone(_IST).date().isoformat(),
                created_at=now,
                updated_at=now,
                strategy_run_id=signal.strategy_run_id,
                signal_id=approval.signal_id,
                parent_order_id=parent_order_id,
                reprice_count=0 if parent is None else parent.reprice_count + 1,
                original_limit_price=(
                    limit if parent is None else parent.original_limit_price or parent.limit_price
                ),
            )
            try:
                await self._journal.create(order)
            except DuplicateRecordError:
                existing = await self._journal.by_key(key)
                if existing is None:
                    raise
                return existing
            return await self._send(order, approval)

    async def cancel(self, order_id: str) -> OrderRecord:
        async with self._lock:
            order = await self._journal.get(order_id)
            if order is None:
                raise ValueError("order not found")
            if OrderState(order.state).terminal or order.state == OrderState.PENDING_CANCEL:
                return order
            if order.state in (OrderState.UNKNOWN, OrderState.PENDING_NEW):
                return await self._resolve(order)
            if order.broker_order_id is None:
                raise ValueError("cannot cancel an uncorrelated order")
            order = await self._change(order, OrderState.PENDING_CANCEL, "cancel requested")
            await self._limiter.acquire("orders")
            try:
                await self._gateway.revoke(
                    CancelOrderRequest(
                        broker_order_id=order.broker_order_id or "",
                        order_type=order.order_type,
                    )
                )
            except Exception as error:
                return await self._change(order, OrderState.UNKNOWN, type(error).__name__)
            # An acknowledgement is not proof of cancellation: confirm through the order book.
            return await self._resolve(order)

    async def refresh(self, order_id: str) -> OrderRecord:
        """Ask the broker again what it holds for one order and record what it says."""
        async with self._lock:
            order = await self._journal.get(order_id)
            if order is None:
                raise ValueError("order not found")
            if OrderState(order.state).terminal:
                return order
            return await self._resolve(order)

    async def recover(self) -> tuple[OrderRecord, ...]:
        """Restart path: verify every order that could still be live against the broker."""
        async with self._lock:
            return tuple([await self._resolve(o) for o in await self._journal.active()])

    async def resolve_unresolved(self) -> tuple[OrderRecord, ...]:
        """Periodic path: only the orders whose state is in doubt."""
        async with self._lock:
            pending = [o for o in await self._journal.active() if o.state in _UNRESOLVED]
            return tuple([await self._resolve(o) for o in pending])

    # --- internals -------------------------------------------------------------------------
    async def _parent(
        self, parent_order_id: str | None, approval: RiskApprovedSignal
    ) -> OrderRecord | None:
        if parent_order_id is None:
            return None
        signal = approval.signal
        parent = await self._journal.get(parent_order_id)
        if (
            parent is None
            or parent.state != OrderState.CANCELLED
            or parent.instrument_id != signal.instrument_id
            or parent.side != signal.side
            or parent.quantity - parent.filled_quantity != signal.quantity
        ):
            raise InvalidReplacementError(
                "replacement requires a confirmed cancelled matching parent"
            )
        return parent

    async def _send(self, order: OrderRecord, approval: RiskApprovedSignal) -> OrderRecord:
        await self._limiter.acquire("orders")
        if self._clock.now() - approval.approved_at > self._approval_lifetime:
            return await self._change(order, OrderState.REJECTED, "approval expired in queue")
        try:
            ack = await self._gateway.submit(
                PlaceOrderRequest(
                    instrument_id=order.instrument_id,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=order.quantity,
                    price=order.limit_price,
                    trigger_price=order.trigger_price,
                    client_tag=order.ordertag,
                )
            )
        except BrokerError as error:
            target = (
                OrderState.UNKNOWN
                if error.classification == ErrorClassification.AMBIGUOUS
                else OrderState.REJECTED
            )
            return await self._change(order, target, str(error))
        except Exception as error:
            return await self._change(order, OrderState.UNKNOWN, type(error).__name__)
        if not ack.broker_order_id or ack.client_tag not in (None, order.ordertag):
            return await self._change(order, OrderState.UNKNOWN, "invalid broker acknowledgement")
        return await self._change(
            order,
            OrderState.OPEN,
            "broker acknowledged intent",
            broker_order_id=ack.broker_order_id,
        )

    async def _resolve(self, order: OrderRecord) -> OrderRecord:
        try:
            matches = await self._gateway.find_by_tag(order.ordertag)
        except Exception:
            return order  # the broker cannot say right now: what we know stays as it is
        if not matches:
            return await self._absent(order)
        if len(matches) > 1:
            return await self._change(
                order, OrderState.UNKNOWN, f"resolution found {len(matches)} matching orders"
            )
        match = matches[0]
        if not self._matches(order, match):
            return await self._change(
                order, OrderState.UNKNOWN, "broker order conflicts with intent"
            )
        target = _BROKER_STATE[match.status]
        if target == OrderState.UNKNOWN:
            return await self._change(order, target, match.status_message or "unrecognised status")
        if match.filled_quantity != order.filled_quantity:
            # The broker holds fills we have not applied (or the reverse). Do not decide the
            # order's fate on partial evidence: fill synchronisation reconciles the quantities.
            # But DO adopt the order's identity — fills can only be attributed to an order whose
            # broker id we know, so withholding it would leave the fills unapplied forever.
            if order.broker_order_id is None and not order.filled_quantity:
                return await self._change(
                    order,
                    OrderState.OPEN,
                    "adopted from the broker; its fills are not applied yet",
                    broker_order_id=match.broker_order_id,
                )
            return order
        if target == OrderState.PARTIALLY_FILLED and not order.filled_quantity:
            target = OrderState.OPEN
        if target == order.state:
            return order if not order.absence_checks else await self._reset_absence(order)
        return await self._change(
            order, target, match.status_message, broker_order_id=match.broker_order_id
        )

    async def _absent(self, order: OrderRecord) -> OrderRecord:
        if order.state not in (OrderState.UNKNOWN, OrderState.PENDING_NEW):
            return await self._change(
                order, OrderState.UNKNOWN, "the broker no longer lists this order"
            )
        checks = order.absence_checks + 1
        if self._absence.confirmed(order, checks, self._clock.now()):
            return await self._change(
                order,
                OrderState.REJECTED,
                f"absent at the broker after {checks} checks over the confirmation window",
                absence_checks=checks,
            )
        return await self._change(
            order,
            OrderState.UNKNOWN,
            f"not found at the broker (check {checks} of {self._absence.confirmations})",
            absence_checks=checks,
        )

    async def _reset_absence(self, order: OrderRecord) -> OrderRecord:
        return await self._change(
            order, OrderState(order.state), "found at the broker", absence_checks=0
        )

    @staticmethod
    def _matches(order: OrderRecord, match: BrokerOrder) -> bool:
        return (
            match.client_tag == order.ordertag
            and match.instrument_id == order.instrument_id
            and match.side == order.side
            and match.order_type == order.order_type
            and match.quantity == order.quantity
            and match.price == order.limit_price
            and match.trigger_price == order.trigger_price
            and order.broker_order_id in (None, match.broker_order_id)
        )

    async def _change(
        self,
        order: OrderRecord,
        target: OrderState,
        reason: str,
        *,
        broker_order_id: str | None = None,
        absence_checks: int | None = None,
    ) -> OrderRecord:
        self._machine.validate(
            OrderState(order.state),
            target,
            order.filled_quantity,
            order.filled_quantity,
            order.quantity,
        )
        if absence_checks is None:
            # Only a still-doubtful order keeps counting; any other outcome ends the count.
            doubtful = target in (OrderState.UNKNOWN, OrderState.PENDING_NEW)
            absence_checks = order.absence_checks if doubtful else 0
        updated = order.model_copy(
            update={
                "state": target.value,
                "status_message": reason,
                "updated_at": self._clock.now(),
                "broker_order_id": broker_order_id or order.broker_order_id,
                "absence_checks": absence_checks,
            }
        )
        await self._journal.transition(order, updated)
        return updated

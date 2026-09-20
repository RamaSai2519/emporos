"""Fault cases prove that replay and ambiguous outcomes never resend an intent."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
from emporos.broker.models import BrokerOrderStatus
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.marketable import MarketableLimit
from emporos.execution.engine import ExecutionEngine
from emporos.execution.errors import (
    ApprovalExpiredError,
    InstrumentFrozenError,
    InvalidOrderPriceError,
    InvalidReplacementError,
)
from emporos.execution.pricing import MarketableLimitPricer
from emporos.execution.resolution import AbsencePolicy
from emporos.execution.state import OrderStateMachine
from tests.support.execution import FixedTicks
from tests.support.strategies import make_signal
from tests.unit.execution.rig import ACCOUNT, ExecutionRig


class TestPlacement:
    async def test_the_intent_is_durable_before_the_broker_is_called(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        assert [e.state for e in rig.journal.events] == ["PENDING_NEW", "OPEN"]
        assert order.state == "OPEN" and order.broker_order_id == "1"
        # FaultGateway asserts the intent is in the journal at the moment of the broker call.
        assert len(rig.gateway.placements) == 1

    async def test_replaying_the_same_approval_returns_the_same_order(self) -> None:
        rig = ExecutionRig()
        approval = await rig.approval()
        first = await rig.engine.place(approval)
        assert await rig.engine.place(approval) == first
        assert await rig.restart().place(approval) == first
        assert len(rig.gateway.placements) == 1

    async def test_the_price_sent_is_marketable_and_on_the_tick_grid(self) -> None:
        rig = ExecutionRig(buffer_bps="10")
        order = await rig.engine.place(await rig.approval())
        assert order.limit_price.amount == Decimal("100.10")  # 100 + 10 bps, ceiled to 0.05
        assert rig.gateway.placements[0].price == order.limit_price

    async def test_an_off_grid_price_is_rounded_toward_the_market_when_priced_by_execution(
        self,
    ) -> None:
        rig = ExecutionRig()
        decision = await rig.risk.review(make_signal(price="100.01"), "off-grid")
        order = await rig.engine.place(decision)  # type: ignore[arg-type]
        assert order.limit_price.amount == Decimal("100.05")  # a buy rounds UP: still marketable

    async def test_an_illegal_price_is_refused_before_anything_is_written(self) -> None:
        rig = ExecutionRig()
        decision = await rig.risk.review(make_signal(price="100.01"), "off-grid")
        with pytest.raises(InvalidOrderPriceError):
            await rig.engine.place(decision, marketable=False)  # type: ignore[arg-type]
        assert rig.journal.orders == {} and rig.gateway.placements == []

    async def test_approval_must_be_fresh_and_typed(self) -> None:
        rig = ExecutionRig()
        approval = await rig.approval()
        rig.wait(6)
        with pytest.raises(ApprovalExpiredError, match="expired"):
            await rig.engine.place(approval)
        with pytest.raises(TypeError, match="RiskApprovedSignal"):
            await rig.engine.place(make_signal())  # type: ignore[arg-type]
        assert rig.gateway.placements == []

    async def test_a_rate_limit_wait_cannot_send_an_expired_approval(self) -> None:
        rig = ExecutionRig()

        class ExpiringLimiter:
            def __init__(self, clock: FixedClock) -> None:
                self._clock = clock

            async def acquire(self, group: str) -> None:
                self._clock.advance(timedelta(seconds=6))

        engine = ExecutionEngine(
            rig.gateway, rig.journal, ExpiringLimiter(rig.clock), rig.clock, IdGenerator(),
            OrderStateMachine(), MarketableLimitPricer(MarketableLimit(Decimal(0)), FixedTicks()),
            ACCOUNT,
        )  # fmt: skip
        result = await engine.place(await rig.approval())
        assert result.state == "REJECTED" and rig.gateway.placements == []

    async def test_a_replacement_cannot_reference_a_working_order(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        with pytest.raises(InvalidReplacementError, match="confirmed cancelled"):
            await rig.engine.place(await rig.approval("replacement"), parent_order_id=order.id)
        assert len(rig.gateway.placements) == 1

    def test_an_engine_needs_an_account_and_a_positive_approval_lifetime(self) -> None:
        rig = ExecutionRig()
        with pytest.raises(ValueError):
            ExecutionEngine(
                rig.gateway,
                rig.journal,
                rig.limiter,
                rig.clock,
                IdGenerator(),
                OrderStateMachine(),
                MarketableLimitPricer(MarketableLimit(Decimal(0)), FixedTicks()),
                "",
            )


class TestAmbiguity:
    async def test_an_ambiguous_placement_is_adopted_after_restart_without_resending(self) -> None:
        rig = ExecutionRig(BrokerTransportError("response lost"))
        approval = await rig.approval()
        order = await rig.engine.place(approval)
        assert order.state == "UNKNOWN"
        with pytest.raises(InstrumentFrozenError, match="frozen"):
            await rig.engine.place(await rig.approval("second"))
        (recovered,) = await rig.restart().recover()
        assert recovered.state == "OPEN"
        assert await rig.restart().place(approval) == recovered
        assert len(rig.gateway.placements) == 1
        assert [e.state for e in rig.journal.events] == ["PENDING_NEW", "UNKNOWN", "OPEN"]

    async def test_a_crash_after_the_intent_before_the_call_never_replays_the_placement(
        self,
    ) -> None:
        rig = ExecutionRig()
        approval = await rig.approval()
        placed = await rig.engine.place(approval)
        rig.journal.orders[placed.id] = rig.journal.events[0]  # the process died right there
        rig.gateway.orders.clear()  # ...and the broker never saw it
        (recovered,) = await rig.restart().recover()
        assert recovered.state == "UNKNOWN" and recovered.absence_checks == 1
        assert await rig.restart().place(approval) == recovered
        assert len(rig.gateway.placements) == 1

    @pytest.mark.parametrize(
        "error,state",
        [(BrokerRejectedError("no margin"), "REJECTED"), (RuntimeError("unparseable"), "UNKNOWN")],
    )
    async def test_classifies_errors(self, error: Exception, state: str) -> None:
        rig = ExecutionRig(error)
        assert (await rig.engine.place(await rig.approval())).state == state

    async def test_an_acknowledgement_without_an_id_is_not_believed(self) -> None:
        rig = ExecutionRig()
        original = rig.gateway.submit

        async def blank(request):  # type: ignore[no-untyped-def]
            ack = await original(request)
            return replace(ack, broker_order_id="")

        rig.gateway.submit = blank  # type: ignore[method-assign]
        assert (await rig.engine.place(await rig.approval())).state == "UNKNOWN"


class TestAbsence:
    """An order the broker does not list is only called absent after repeated, spread-out checks."""

    async def _lost(self, policy: AbsencePolicy) -> tuple[ExecutionRig, str]:
        rig = ExecutionRig(absence=policy)
        placed = await rig.engine.place(await rig.approval())
        rig.journal.orders[placed.id] = rig.journal.events[0]  # PENDING_NEW, never reached broker
        rig.gateway.orders.clear()
        return rig, placed.id

    async def test_absence_needs_enough_checks_and_enough_time(self) -> None:
        rig, order_id = await self._lost(
            AbsencePolicy(confirmations=3, window=timedelta(seconds=30))
        )
        engine = rig.restart()
        for expected in (1, 2):
            (order,) = await engine.recover()
            assert (order.state, order.absence_checks) == ("UNKNOWN", expected)
        (still,) = await engine.recover()  # third check, but the window has not elapsed
        assert still.state == "UNKNOWN" and still.absence_checks == 3
        rig.wait(31)
        (rejected,) = await engine.recover()
        assert rejected.state == "REJECTED" and rejected.absence_checks == 4
        assert await engine.recover() == ()
        assert rig.journal.orders[order_id].state == "REJECTED"

    async def test_the_count_survives_a_restart(self) -> None:
        rig, _ = await self._lost(AbsencePolicy(confirmations=2, window=timedelta(seconds=1)))
        await rig.restart().recover()
        rig.wait(2)
        (order,) = await rig.restart().recover()  # a brand-new engine, same journal
        assert order.state == "REJECTED"

    async def test_an_order_that_turns_up_ends_the_wait_and_resets_the_count(self) -> None:
        rig, order_id = await self._lost(
            AbsencePolicy(confirmations=3, window=timedelta(seconds=1))
        )
        engine = rig.restart()
        await engine.recover()
        placement = rig.gateway.placements[0]
        rig.gateway.error = None
        await rig.gateway.submit(placement)  # the broker turns out to have it after all
        (order,) = await engine.recover()
        assert order.state == "OPEN" and order.absence_checks == 0
        assert rig.journal.orders[order_id].broker_order_id == "2"

    async def test_a_lookup_that_fails_changes_nothing(self) -> None:
        rig, order_id = await self._lost(AbsencePolicy())
        rig.gateway.lookup_error = BrokerTransportError("down")
        before = rig.journal.orders[order_id]
        (order,) = await rig.restart().recover()
        assert order == before and rig.journal.orders[order_id] == before

    async def test_an_open_order_the_broker_stops_listing_becomes_unknown_not_absent(self) -> None:
        rig = ExecutionRig()
        await rig.engine.place(await rig.approval())
        rig.gateway.orders.clear()
        (order,) = await rig.engine.recover()
        assert order.state == "UNKNOWN" and order.absence_checks == 0

    def test_a_policy_must_be_meaningful(self) -> None:
        with pytest.raises(ValueError):
            AbsencePolicy(confirmations=0)
        with pytest.raises(ValueError):
            AbsencePolicy(window=timedelta(0))


class TestCancellation:
    async def test_cancel_is_confirmed_and_a_terminal_replay_is_a_noop(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        cancelled = await rig.engine.cancel(order.id)
        assert cancelled.state == "CANCELLED"
        assert await rig.engine.cancel(order.id) == cancelled
        assert rig.limiter.calls == ["orders", "orders"]
        with pytest.raises(ValueError, match="not found"):
            await rig.engine.cancel("missing")

    async def test_a_failed_cancel_call_makes_the_order_unknown_then_resolved(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.gateway.cancel_error = TimeoutError()
        assert (await rig.engine.cancel(order.id)).state == "UNKNOWN"
        assert (await rig.engine.cancel(order.id)).state == "OPEN"

    async def test_a_cancel_is_not_final_while_the_broker_holds_unapplied_fills(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.gateway.fill(0, 4, "100", "T1", rig.clock.now())
        rig.gateway.orders[0] = replace(
            rig.gateway.orders[0], status=BrokerOrderStatus.CANCELLED
        )  # filled 4 of 10, then cancelled — but we have not applied the 4 yet
        stalled = await rig.engine.cancel(order.id)
        assert stalled.state == "PENDING_CANCEL"
        assert stalled.filled_quantity == 0
        await rig.sync.sync()
        settled = await rig.engine.refresh(order.id)
        assert (settled.state, settled.filled_quantity) == ("CANCELLED", 4)

    async def test_an_order_without_a_broker_id_cannot_be_cancelled(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.journal.orders[order.id] = order.model_copy(update={"broker_order_id": None})
        with pytest.raises(ValueError, match="uncorrelated"):
            await rig.engine.cancel(order.id)


class TestResolution:
    async def test_multiple_and_mismatching_broker_orders_stay_unknown(self) -> None:
        rig = ExecutionRig(BrokerTransportError("lost"))
        await rig.engine.place(await rig.approval())
        original = rig.gateway.orders[0]
        rig.gateway.orders.append(original)
        assert (await rig.engine.recover())[0].state == "UNKNOWN"
        rig.gateway.orders = [replace(original, quantity=original.quantity + 1)]
        assert (await rig.engine.recover())[0].state == "UNKNOWN"

    async def test_an_unrecognised_status_is_unknown_never_guessed(self) -> None:
        rig = ExecutionRig()
        await rig.engine.place(await rig.approval())
        rig.gateway.orders = [replace(rig.gateway.orders[0], status=BrokerOrderStatus.UNRECOGNISED)]
        assert (await rig.engine.recover())[0].state == "UNKNOWN"

    async def test_a_broker_rejection_is_recorded_and_terminal(self) -> None:
        rig = ExecutionRig(BrokerTransportError("lost"))
        await rig.engine.place(await rig.approval())
        rig.gateway.orders = [replace(rig.gateway.orders[0], status=BrokerOrderStatus.REJECTED)]
        assert (await rig.engine.recover())[0].state == "REJECTED"

    async def test_recovery_leaves_fill_quantities_to_fill_processing(self) -> None:
        rig = ExecutionRig()
        await rig.engine.place(await rig.approval())
        rig.gateway.fill(0, 10, "100", "T1", rig.clock.now())
        assert (await rig.engine.recover())[0].state == "OPEN"  # not decided on partial evidence
        await rig.sync.sync()
        assert await rig.engine.recover() == ()
        assert rig.journal.orders[next(iter(rig.journal.orders))].state == "FILLED"

    async def test_resolve_unresolved_touches_only_orders_in_doubt(self) -> None:
        rig = ExecutionRig()
        await rig.engine.place(await rig.approval())
        assert await rig.engine.resolve_unresolved() == ()
        rig.gateway.error = BrokerTransportError("lost")
        rig.wait(1)
        doubtful = await rig.engine.place(await rig.approval("second"))
        assert doubtful.state == "UNKNOWN"
        (resolved,) = await rig.engine.resolve_unresolved()
        assert resolved.state == "OPEN"

    async def test_refresh_of_a_missing_order_is_an_error_and_of_a_terminal_one_a_noop(
        self,
    ) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        cancelled = await rig.engine.cancel(order.id)
        assert await rig.engine.refresh(order.id) == cancelled
        with pytest.raises(ValueError, match="not found"):
            await rig.engine.refresh("missing")


class TestAudit:
    async def test_an_orders_history_is_reconstructable_from_its_events_alone(self) -> None:
        rig = ExecutionRig(BrokerTransportError("lost"))
        order = await rig.engine.place(await rig.approval())
        await rig.engine.recover()
        rig.gateway.fill(0, 4, "100", "T1", rig.clock.now())
        await rig.sync.sync()
        rig.gateway.fill(0, 6, "100.05", "T2", rig.clock.now())
        await rig.sync.sync()
        events = await rig.journal.history(order.id)
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert [(e.state, e.filled_quantity) for e in events] == [
            ("PENDING_NEW", 0),
            ("UNKNOWN", 0),
            ("OPEN", 0),
            ("PARTIALLY_FILLED", 4),
            ("FILLED", 10),
        ]
        final = rig.journal.orders[order.id]
        assert (events[-1].state, events[-1].filled_quantity) == (
            final.state,
            final.filled_quantity,
        )

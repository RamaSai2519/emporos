"""Repricing cannot increase remaining size or skip confirmation and fresh risk."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.broker.models import BrokerOrderStatus
from emporos.domain.money import Money
from emporos.domain.orders import OrderType
from emporos.execution.repricing import RepriceCoordinator, RepricePolicy
from emporos.session.replacement import RiskReplacementReviewer
from tests.support.records import RecordFactory
from tests.unit.execution.rig import ExecutionRig


class TestRepricePolicy:
    @pytest.mark.parametrize(
        "count,age,original,candidate",
        [
            (2, 6, "100", "100"),
            (-1, 6, "100", "100"),
            (0, 1, "100", "100"),
            (0, 6, "0", "100"),
            (0, 6, "100", "0"),
            (0, 6, "100", "102"),
        ],
    )
    def test_bounds(self, count, age, original, candidate):
        policy = RepricePolicy(timedelta(seconds=5), 2, Decimal("100"))
        with pytest.raises(ValueError):
            policy.validate(
                RecordFactory().order(),
                count,
                Money.of(original),
                Money.of(candidate),
                timedelta(seconds=age),
            )

    def test_stop_orders_cannot_be_chased_and_policy_must_be_valid(self):
        with pytest.raises(ValueError):
            RepricePolicy(timedelta(0), 1, Decimal("100"))
        policy = RepricePolicy(timedelta(seconds=5), 2, Decimal("100"))
        with pytest.raises(ValueError, match="stop-loss"):
            policy.validate(
                RecordFactory().order(order_type=OrderType.STOPLOSS_LIMIT),
                0,
                Money.of("100"),
                Money.of("100"),
                timedelta(seconds=6),
            )


class TestRepriceCoordinator:
    def _coordinator(self, rig: ExecutionRig) -> RepriceCoordinator:
        return RepriceCoordinator(
            rig.engine,
            RiskReplacementReviewer(rig.risk, rig.clock),
            RepricePolicy(timedelta(seconds=5), 2, Decimal("100")),
            rig.clock,
            rig.sync,
        )

    async def test_confirm_cancel_then_replace_remaining_with_new_identity(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.clock.advance(timedelta(seconds=6))
        result = await self._coordinator(rig).reprice(order, order.limit_price)
        assert result.id != order.id
        assert len(rig.gateway.placements) == 2
        assert rig.journal.orders[order.id].state == "CANCELLED"
        assert result.quantity == order.quantity
        assert result.parent_order_id == order.id and result.reprice_count == 1
        assert result.signal_kind == "ENTRY" and result.strategy_run_id == order.strategy_run_id

    async def test_it_works_from_the_persisted_order_alone_so_a_restart_changes_nothing(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.clock.advance(timedelta(seconds=6))
        reloaded = rig.journal.orders[order.id]  # nothing from the original approval survives
        rig.engine = rig.restart()
        result = await self._coordinator(rig).reprice(reloaded, reloaded.limit_price)
        assert result.parent_order_id == order.id

    async def test_an_order_that_did_not_come_from_a_signal_is_not_repriced(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        anonymous = order.model_copy(update={"signal_kind": None})
        with pytest.raises(ValueError, match="came from a signal"):
            await self._coordinator(rig).reprice(anonymous, order.limit_price)

    async def test_a_fill_racing_the_cancel_is_applied_and_nothing_is_replaced(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.gateway.fill(0, order.quantity, "100", "T1", rig.clock.now())  # filled before cancel
        rig.clock.advance(timedelta(seconds=6))
        result = await self._coordinator(rig).reprice(order, order.limit_price)
        assert (result.state, result.filled_quantity) == ("FILLED", order.quantity)
        assert len(rig.gateway.placements) == 1

    async def test_a_partial_fill_before_the_cancel_shrinks_the_replacement(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.gateway.fill(0, 4, "100", "T1", rig.clock.now())
        await rig.sync.sync()
        order = await rig.engine.refresh(order.id)
        rig.clock.advance(timedelta(seconds=6))
        result = await self._coordinator(rig).reprice(order, order.limit_price)
        assert result.quantity == 6 and result.parent_order_id == order.id
        assert rig.journal.orders[order.id].state == "CANCELLED"

    async def test_a_broker_that_says_filled_without_trades_is_not_replaced_over(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.gateway.orders = [
            replace(
                rig.gateway.orders[0],
                status=BrokerOrderStatus.FILLED,
                filled_quantity=order.quantity,
            )
        ]  # the order book says filled, but the trade book has nothing: we cannot know
        rig.clock.advance(timedelta(seconds=6))
        result = await self._coordinator(rig).reprice(order, order.limit_price)
        assert result.state == "UNKNOWN" and len(rig.gateway.placements) == 1

    async def test_a_reprice_chain_terminates_within_the_bound_and_is_fully_traceable(self):
        rig = ExecutionRig()
        first = await rig.engine.place(await rig.approval())
        coordinator = self._coordinator(rig)  # max_reprices = 2
        chain = [first]
        for count in (0, 1):
            rig.clock.advance(timedelta(seconds=6))
            latest = rig.journal.orders[chain[-1].id]
            result = await coordinator.reprice(latest, first.limit_price)
            assert result.parent_order_id == latest.id and result.reprice_count == count + 1
            chain.append(result)
        rig.clock.advance(timedelta(seconds=6))
        with pytest.raises(ValueError, match="count bound"):
            await coordinator.reprice(chain[-1], first.limit_price)
        # Walking parent links from the last order reconstructs the whole chain, oldest first.
        walked, cursor = [], chain[-1]
        while cursor is not None:
            walked.append(cursor.id)
            cursor = rig.journal.orders.get(cursor.parent_order_id or "")
        assert walked[::-1] == [o.id for o in chain]
        assert {o.original_limit_price for o in chain} == {first.limit_price}
        assert len({o.idempotency_key for o in chain}) == 3
        assert len(rig.gateway.placements) == 3

    async def test_the_chase_distance_never_exceeds_the_bound(self):
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        rig.clock.advance(timedelta(seconds=6))
        too_far = Money(order.limit_price.amount * Decimal("1.02"))  # 200 bps; the bound is 100
        with pytest.raises(ValueError, match="chase"):
            await self._coordinator(rig).reprice(order, too_far)
        assert rig.journal.orders[order.id].state == "OPEN"  # nothing was cancelled either

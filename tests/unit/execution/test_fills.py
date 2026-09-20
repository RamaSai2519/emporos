"""Fill processing is idempotent, atomic and the only thing that moves an order's quantity."""

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.broker.models import BrokerTrade
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.execution.fills import FillProcessor, FillResult
from emporos.persistence.errors import ConcurrentModificationError
from emporos.persistence.records import OrderRecord
from tests.support.execution import MemoryOrderJournal
from tests.unit.execution.rig import ACCOUNT, ExecutionRig


async def placed(rig: ExecutionRig) -> OrderRecord:
    return await rig.engine.place(await rig.approval())


def trade(rig: ExecutionRig, trade_id: str, qty: int, price: str, **kw: object) -> BrokerTrade:
    order = rig.gateway.orders[0]
    fields: dict[str, object] = {
        "trade_id": trade_id, "broker_order_id": order.broker_order_id,
        "instrument_id": order.instrument_id, "side": order.side, "quantity": qty,
        "price": Money.of(price), "executed_at": rig.clock.now(),
    }  # fmt: skip
    return BrokerTrade(**{**fields, **kw})  # type: ignore[arg-type]


class TestApplyingFills:
    async def test_partial_then_complete_fills_walk_the_order_to_filled(self) -> None:
        rig = ExecutionRig()
        order = await placed(rig)
        assert await rig.processor.process(trade(rig, "T1", 4, "100")) == FillResult.APPLIED
        mid = rig.journal.orders[order.id]
        assert (mid.state, mid.filled_quantity, mid.average_price) == (
            "PARTIALLY_FILLED", 4, Money.of("100")
        )  # fmt: skip
        assert await rig.processor.process(trade(rig, "T2", 6, "101")) == FillResult.APPLIED
        done = rig.journal.orders[order.id]
        assert (done.state, done.filled_quantity) == ("FILLED", 10)
        assert done.average_price == Money.of("100.6")  # (4*100 + 6*101) / 10
        assert [e.session_trade_no for e in rig.journal.executions.values()] == [1, 2]

    async def test_the_position_follows_the_fills_with_charges(self) -> None:
        rig = ExecutionRig()
        await placed(rig)
        await rig.processor.process(trade(rig, "T1", 10, "100"))
        (position,) = rig.journal.positions.values()
        assert (position.net_quantity, position.average_price) == (10, Money.of("100"))
        assert position.realised_pnl == Money.zero()

    async def test_redelivering_a_fill_changes_nothing(self) -> None:
        rig = ExecutionRig()
        order = await placed(rig)
        first = trade(rig, "T1", 4, "100")
        assert await rig.processor.process(first) == FillResult.APPLIED
        snapshot = (
            dict(rig.journal.orders), dict(rig.journal.positions), dict(rig.journal.executions),
            len(rig.journal.event_log[order.id]),
        )  # fmt: skip
        for _ in range(3):
            assert await rig.processor.process(first) == FillResult.DUPLICATE
        assert snapshot == (
            dict(rig.journal.orders), dict(rig.journal.positions), dict(rig.journal.executions),
            len(rig.journal.event_log[order.id]),
        )  # fmt: skip
        assert len(rig.journal.executions) == 1

    async def test_a_fill_for_an_order_we_do_not_know_is_left_alone(self) -> None:
        rig = ExecutionRig()
        await placed(rig)
        stranger = trade(rig, "T9", 1, "100", broker_order_id="not-ours")
        assert await rig.processor.process(stranger) == FillResult.UNATTRIBUTED
        assert rig.journal.executions == {} and rig.journal.positions == {}

    async def test_an_overfill_is_refused_and_writes_nothing(self) -> None:
        rig = ExecutionRig()
        await placed(rig)
        assert await rig.processor.process(trade(rig, "T1", 11, "100")) == FillResult.REFUSED
        assert rig.journal.executions == {}

    async def test_a_trade_contradicting_its_order_is_refused(self) -> None:
        rig = ExecutionRig()
        await placed(rig)
        wrong_side = trade(rig, "T1", 1, "100", side=OrderSide.SELL)
        wrong_stock = trade(rig, "T2", 1, "100", instrument_id="NSE:OTHER")
        for bad in (wrong_side, wrong_stock):
            assert await rig.processor.process(bad) == FillResult.REFUSED
        assert rig.journal.executions == {}

    async def test_a_fill_after_a_cancel_is_recorded_without_reopening_the_order(self) -> None:
        rig = ExecutionRig()
        order = await placed(rig)
        await rig.engine.cancel(order.id)
        assert await rig.processor.process(trade(rig, "T1", 3, "100")) == FillResult.APPLIED
        late = rig.journal.orders[order.id]
        assert (late.state, late.filled_quantity) == ("CANCELLED", 3)

    async def test_a_partial_fill_while_a_cancel_is_pending_keeps_the_cancel_pending(self) -> None:
        rig = ExecutionRig()
        order = await placed(rig)
        rig.journal.orders[order.id] = order.model_copy(update={"state": "PENDING_CANCEL"})
        await rig.processor.process(trade(rig, "T1", 3, "100"))
        assert rig.journal.orders[order.id].state == "PENDING_CANCEL"
        await rig.processor.process(trade(rig, "T2", 7, "100"))
        assert rig.journal.orders[order.id].state == "FILLED"

    async def test_a_short_sequence_matches_a_hand_computed_result(self) -> None:
        """Buy 10 @100, buy 10 @102 (average 101), sell 15 @103: realised 15 * (103 - 101)."""
        rig = ExecutionRig()
        await placed(rig)
        await rig.processor.process(trade(rig, "T1", 10, "100"))
        rig.wait(1)
        second = await rig.engine.place(await rig.approval("two"))
        rig.gateway.orders[1] = rig.gateway.orders[1]
        buy2 = BrokerTrade("T2", second.broker_order_id or "", second.instrument_id, OrderSide.BUY,
                           10, Money.of("102"), rig.clock.now())  # fmt: skip
        await rig.processor.process(buy2)
        (position,) = rig.journal.positions.values()
        assert (position.net_quantity, position.average_price) == (20, Money.of("101"))
        rig.wait(1)
        sell = await rig.engine.place(await rig.risk.review(  # type: ignore[arg-type]
            __import__("tests.support.strategies", fromlist=["make_signal"]).make_signal(
                side=OrderSide.SELL, quantity=15, price="103"), "three"))  # fmt: skip
        await rig.processor.process(
            BrokerTrade("T3", sell.broker_order_id or "", sell.instrument_id, OrderSide.SELL, 15,
                        Money.of("103"), rig.clock.now())
        )  # fmt: skip
        (position,) = rig.journal.positions.values()
        assert (position.net_quantity, position.realised_pnl) == (5, Money.of("30"))
        assert position.average_price == Money.of("101")


class TestConcurrency:
    async def test_a_change_under_our_feet_is_retried_against_fresh_state(self) -> None:
        rig = ExecutionRig()
        await placed(rig)

        class Flaky(MemoryOrderJournal):
            failures = 1

        real = rig.journal
        original = real.record_fill

        async def once_stale(*args, **kwargs):  # type: ignore[no-untyped-def]
            if once_stale.calls == 0:
                once_stale.calls += 1
                raise ConcurrentModificationError("moved")
            return await original(*args, **kwargs)

        once_stale.calls = 0  # type: ignore[attr-defined]
        real.record_fill = once_stale  # type: ignore[method-assign]
        assert await rig.processor.process(trade(rig, "T1", 10, "100")) == FillResult.APPLIED

    async def test_a_order_that_never_stops_changing_is_reported(self) -> None:
        rig = ExecutionRig()
        await placed(rig)

        async def always_stale(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise ConcurrentModificationError("moved")

        rig.journal.record_fill = always_stale  # type: ignore[method-assign]
        with pytest.raises(ConcurrentModificationError):
            await rig.processor.process(trade(rig, "T1", 10, "100"))


class TestSynchroniser:
    async def test_the_whole_trade_book_is_applied_once_however_often_it_is_read(self) -> None:
        rig = ExecutionRig()
        order = await placed(rig)
        rig.gateway.fill(0, 4, "100", "T1", rig.clock.now())
        rig.gateway.fill(0, 6, "101", "T2", rig.clock.now() + timedelta(seconds=1))
        first = await rig.sync.sync()
        assert (first.applied, first.duplicates) == (2, 0)
        again = await rig.sync.sync()
        assert (again.applied, again.duplicates) == (0, 2)
        assert rig.journal.orders[order.id].state == "FILLED"
        assert len(rig.journal.executions) == 2

    async def test_trades_are_applied_in_time_order_whatever_order_the_broker_lists_them(
        self,
    ) -> None:
        rig = ExecutionRig()
        await placed(rig)
        rig.gateway.fill(0, 4, "100", "B", rig.clock.now() + timedelta(seconds=5))
        rig.gateway.fill(0, 6, "101", "A", rig.clock.now() + timedelta(seconds=9))
        rig.gateway.trades.reverse()
        await rig.sync.sync()
        assert [e.broker_trade_id for e in sorted(
            rig.journal.executions.values(), key=lambda e: e.session_trade_no or 0
        )] == ["B", "A"]  # fmt: skip

    async def test_unattributed_and_refused_trades_are_counted_not_hidden(self) -> None:
        rig = ExecutionRig()
        await placed(rig)
        rig.gateway.trades.append(trade(rig, "X1", 1, "100", broker_order_id="manual-order"))
        rig.gateway.trades.append(trade(rig, "X2", 99, "100"))
        result = await rig.sync.sync()
        assert (result.applied, result.unattributed, result.refused) == (0, 1, 1)

    async def test_fees_come_from_the_injected_cost_model(self) -> None:
        rig = ExecutionRig()
        await placed(rig)

        class Flat:
            def charges(self, trade: BrokerTrade) -> Money:
                return Money.of("20")

        processor = FillProcessor(
            rig.journal, Flat(), rig.processor._calculator, rig.processor._machine,
            rig.clock, rig.processor._ids, ACCOUNT,
        )  # fmt: skip
        await processor.process(trade(rig, "T1", 10, "100"))
        (position,) = rig.journal.positions.values()
        assert position.fees == Money.of("20") and position.realised_pnl == Money.of("-20")
        assert Decimal(1) == 1

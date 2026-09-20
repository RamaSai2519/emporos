"""Discrepancies must be visible and halt trading, never silently auto-healed."""

from dataclasses import replace
from datetime import timedelta

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerPosition, BrokerTrade
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.portfolio.ledger import PositionCalculator
from emporos.portfolio.reconciliation import (
    ADOPTABLE,
    DiscrepancyKind,
    Reconciler,
    ReconciliationComparator,
    ReconciliationSnapshot,
    ReconciliationTracker,
    ReconciliationTrigger,
)
from emporos.portfolio.replay import PositionReplay
from emporos.risk.snapshot import ReconciliationStatus
from tests.support.fakes import RecordingAlertSink
from tests.support.records import NOW, RecordFactory


class ReconciliationRig:
    def __init__(self):
        factory = RecordFactory()
        order = factory.order(broker_order_id="b", state="FILLED", filled_quantity=10)
        fill = factory.execution(
            order_id=order.id, instrument_id=order.instrument_id, price=order.limit_price
        )
        position = factory.position(instrument_id=order.instrument_id, average_price=fill.price)
        self.data = ReconciliationSnapshot(
            (order,),
            (fill,),
            (position,),
            (
                BrokerOrder(
                    "b",
                    order.ordertag,
                    order.instrument_id,
                    order.side,
                    order.order_type,
                    10,
                    10,
                    BrokerOrderStatus.FILLED,
                    price=order.limit_price,
                ),
            ),
            (
                BrokerTrade(
                    fill.broker_trade_id,
                    "b",
                    fill.instrument_id,
                    fill.side,
                    fill.quantity,
                    fill.price,
                ),
            ),
            (BrokerPosition(position.instrument_id, 10, position.average_price),),
        )
        self.records = []
        self.halts = []
        self.error = False
        self.adoptions = 0
        self.on_adopt = None

    async def snapshot(self):
        if self.error:
            raise OSError("offline")
        return self.data

    async def record(self, record):
        self.records.append(record)

    async def halt(self, reason):
        self.halts.append(reason)

    async def adopt(self):
        self.adoptions += 1
        if self.on_adopt:
            self.data = self.on_adopt(self.data)


class TestReconciliation:
    def test_clean_snapshot(self):
        assert ReconciliationComparator().compare(ReconciliationRig().data) == ()

    @pytest.mark.parametrize(
        "field,kind",
        [
            ("broker_orders", DiscrepancyKind.MISSING_BROKER_ORDER),
            ("orders", DiscrepancyKind.EXTERNAL_ORDER),
            ("broker_trades", DiscrepancyKind.MISSING_FILL),
            ("executions", DiscrepancyKind.EXTERNAL_FILL),
            ("positions", DiscrepancyKind.ORPHAN_POSITION),
            ("broker_positions", DiscrepancyKind.ORPHAN_POSITION),
        ],
    )
    def test_absent_counterparts(self, field, kind):
        data = replace(ReconciliationRig().data, **{field: ()})
        assert kind in {d.kind for d in ReconciliationComparator().compare(data)}

    def test_conflicting_details_and_duplicate_broker_ids(self):
        data = ReconciliationRig().data
        bad = replace(
            data,
            broker_orders=(replace(data.broker_orders[0], quantity=11),) * 2,
            broker_trades=(replace(data.broker_trades[0], price=Money.of("1")),) * 2,
            broker_positions=(replace(data.broker_positions[0], net_quantity=11),),
        )
        assert {d.kind for d in ReconciliationComparator().compare(bad)} == {
            DiscrepancyKind.ORDER_MISMATCH,
            DiscrepancyKind.DUPLICATE_IDENTITY,
            DiscrepancyKind.FILL_MISMATCH,
            DiscrepancyKind.POSITION_MISMATCH,
        }

    def test_every_kind_in_the_taxonomy_is_covered_by_a_test(self):
        covered = {
            "MISSING_BROKER_ORDER", "EXTERNAL_ORDER", "ORDER_MISMATCH", "MISSING_FILL",
            "FILL_MISMATCH", "EXTERNAL_FILL", "POSITION_MISMATCH", "ORPHAN_POSITION",
            "DUPLICATE_IDENTITY", "POSITION_HISTORY_MISMATCH", "ORDER_FILL_MISMATCH",
        }  # fmt: skip
        assert {k.value for k in DiscrepancyKind} == covered


class TestOrderMatching:
    def test_a_rejected_order_the_broker_never_saw_is_not_a_discrepancy(self):
        data = ReconciliationRig().data
        rejected = RecordFactory().order(state="REJECTED", broker_order_id=None)
        assert (
            ReconciliationComparator().compare(replace(data, orders=(*data.orders, rejected))) == ()
        )

    def test_an_order_still_being_placed_is_left_to_execution(self):
        data = ReconciliationRig().data
        for state in ("PENDING_NEW", "UNKNOWN"):
            pending = RecordFactory().order(state=state, broker_order_id=None)
            assert (
                ReconciliationComparator().compare(replace(data, orders=(*data.orders, pending)))
                == ()
            )

    def test_a_working_order_with_no_broker_id_and_no_broker_copy_is_missing(self):
        data = ReconciliationRig().data
        ghost = RecordFactory().order(state="OPEN", broker_order_id=None)
        found = ReconciliationComparator().compare(replace(data, orders=(*data.orders, ghost)))
        assert [d.kind for d in found] == [DiscrepancyKind.MISSING_BROKER_ORDER]

    def test_an_order_is_ours_if_the_broker_holds_it_under_our_tag_even_without_its_id(self):
        data = ReconciliationRig().data
        ours = data.orders[0].model_copy(update={"broker_order_id": None})
        alone = replace(data, orders=(ours,), executions=(), broker_trades=(), positions=(),
                        broker_positions=())  # fmt: skip
        assert ReconciliationComparator().compare(alone) == ()

    def test_a_rejected_order_the_broker_lists_by_our_tag_is_ours_not_external(self):
        factory = RecordFactory()
        order = factory.order(state="REJECTED", broker_order_id=None)
        listed = BrokerOrder(
            "r1", order.ordertag, order.instrument_id, order.side, order.order_type,
            order.quantity, 0, BrokerOrderStatus.REJECTED, price=order.limit_price,
        )  # fmt: skip
        data = ReconciliationSnapshot((order,), (), (), (listed,), (), ())
        assert ReconciliationComparator().compare(data) == ()

    def test_an_in_flight_state_matches_whatever_the_broker_says_but_facts_must_agree(self):
        data = ReconciliationRig().data
        cancelling = data.orders[0].model_copy(
            update={"state": "PENDING_CANCEL", "filled_quantity": 10}
        )
        assert ReconciliationComparator().compare(replace(data, orders=(cancelling,))) == ()
        wrong = cancelling.model_copy(update={"quantity": 99})
        found = ReconciliationComparator().compare(replace(data, orders=(wrong,)))
        assert DiscrepancyKind.ORDER_MISMATCH in {d.kind for d in found}

    def test_a_broker_id_that_contradicts_ours_is_a_mismatch(self):
        data = ReconciliationRig().data
        wrong = data.orders[0].model_copy(update={"broker_order_id": "someone-elses"})
        kinds = {d.kind for d in ReconciliationComparator().compare(replace(data, orders=(wrong,)))}
        assert DiscrepancyKind.ORDER_MISMATCH in kinds


class TestInternalConsistency:
    """No broker needed: our own rows must follow from our own fills."""

    def comparator(self):
        return ReconciliationComparator(PositionReplay(PositionCalculator()))

    def data(self):
        rig = ReconciliationRig()
        d = rig.data
        position = d.positions[0].model_copy(update={"account_id": "acct"})
        fill = d.executions[0].model_copy(
            update={"account_id": "acct", "side": d.orders[0].side, "quantity": 10}
        )
        return replace(d, executions=(fill,), positions=(position,), history=(fill,)), fill

    def test_consistent_books_are_clean(self):
        data, fill = self.data()
        position = PositionCalculator().apply(
            data.positions[0].model_copy(
                update={
                    "net_quantity": 0, "average_price": Money.zero(), "realised_pnl": Money.zero(),
                    "fees": Money.zero(), "gross_realised_pnl": Money.zero(),
                }
            ),
            fill,
        )  # fmt: skip
        clean = replace(
            data,
            positions=(position,),
            broker_positions=(BrokerPosition(position.instrument_id, 10, position.average_price),),
        )
        assert self.comparator().compare(clean) == ()

    def test_a_position_its_fills_contradict_is_named(self):
        data, _ = self.data()
        drifted = data.positions[0].model_copy(update={"net_quantity": 7})
        found = self.comparator().compare(replace(data, positions=(drifted,)))
        assert DiscrepancyKind.POSITION_HISTORY_MISMATCH in {d.kind for d in found}

    def test_an_order_whose_quantity_its_fills_contradict_is_named(self):
        data, fill = self.data()
        short = data.orders[0].model_copy(update={"filled_quantity": 4})
        found = self.comparator().compare(replace(data, orders=(short,)))
        assert DiscrepancyKind.ORDER_FILL_MISMATCH in {d.kind for d in found}

    def test_without_a_history_the_check_is_not_pretended(self):
        data, _ = self.data()
        kinds = {d.kind for d in self.comparator().compare(replace(data, history=None))}
        assert DiscrepancyKind.POSITION_HISTORY_MISMATCH not in kinds


class TestReconciler:
    def service(self, rig, *, tracker=None):
        self.tracker = tracker or ReconciliationTracker(FixedClock(NOW))
        return Reconciler(
            rig, ReconciliationComparator(), rig, rig, rig, self.tracker,
            FixedClock(NOW), IdGenerator(), RecordingAlertSink(),
        )  # fmt: skip

    async def test_a_clean_run_is_persisted_and_leaves_trading_alone(self):
        rig = ReconciliationRig()
        assert await self.service(rig).run(ReconciliationTrigger.STARTUP) == ()
        record = rig.records[-1]
        assert (record.status, record.trigger) == ("CLEAN", "STARTUP")
        assert record.discrepancies == [] and rig.halts == [] and rig.adoptions == 0
        assert self.tracker.status() is ReconciliationStatus.CLEAN

    async def test_a_fill_the_broker_has_and_we_lack_is_adopted_and_the_run_is_healed(self):
        rig = ReconciliationRig()
        real = rig.data
        rig.data = replace(real, executions=(), positions=(), history=None)  # we lag the broker
        rig.on_adopt = lambda _: real  # adopting the fills brings our books level
        assert await self.service(rig).run() == ()
        record = rig.records[-1]
        assert record.status == "HEALED" and rig.adoptions == 1 and rig.halts == []
        assert {h["kind"] for h in record.healed} <= {
            *(k.value for k in ADOPTABLE),
            "ORPHAN_POSITION",
        }
        assert self.tracker.status() is ReconciliationStatus.CLEAN

    async def test_adoption_that_does_not_explain_everything_still_halts(self):
        rig = ReconciliationRig()
        rig.data = replace(rig.data, executions=())  # adoptable, but adoption changes nothing
        found = await self.service(rig).run()
        assert found and rig.adoptions == 1 and len(rig.halts) == 1
        assert rig.records[-1].status == "FAILED"
        assert self.tracker.status() is ReconciliationStatus.FAILED

    @pytest.mark.parametrize(
        "field",
        ["broker_orders", "orders", "broker_trades", "positions", "broker_positions"],
    )
    async def test_an_ambiguous_discrepancy_is_never_auto_healed(self, field):
        rig = ReconciliationRig()
        rig.data = replace(rig.data, **{field: ()})
        found = await self.service(rig).run()
        assert found and len(rig.halts) == 1
        assert rig.adoptions == 0 if any(d.kind not in ADOPTABLE for d in found) else True

    async def test_one_ambiguous_discrepancy_among_adoptable_ones_prevents_adoption(self):
        rig = ReconciliationRig()
        rig.data = replace(rig.data, executions=(), broker_orders=())  # adoptable + ambiguous
        await self.service(rig).run()
        assert rig.adoptions == 0 and len(rig.halts) == 1

    async def test_missing_evidence_halts_and_reraises(self):
        rig = ReconciliationRig()
        service = self.service(rig)
        rig.error = True
        with pytest.raises(OSError):
            await service.run()
        assert len(rig.halts) == 1 and rig.records == []
        assert self.tracker.status() is ReconciliationStatus.FAILED


class TestTracker:
    def test_it_is_pending_until_a_run_is_clean_and_fails_safe_when_stale(self):
        clock = FixedClock(NOW)
        tracker = ReconciliationTracker(clock, max_age=timedelta(minutes=10))
        assert tracker.status() is ReconciliationStatus.PENDING
        tracker.clean()
        assert tracker.status() is ReconciliationStatus.CLEAN
        clock.advance(timedelta(minutes=10))
        assert tracker.status() is ReconciliationStatus.CLEAN
        clock.advance(timedelta(seconds=1))
        assert tracker.status() is ReconciliationStatus.PENDING  # stopped running == not clean
        tracker.failed()
        assert tracker.status() is ReconciliationStatus.FAILED
        tracker.clean()
        assert tracker.status() is ReconciliationStatus.CLEAN

    def test_the_age_limit_must_be_positive(self):
        with pytest.raises(ValueError):
            ReconciliationTracker(FixedClock(NOW), max_age=timedelta(0))

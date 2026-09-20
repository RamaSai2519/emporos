"""Discrepancies must be visible and halt trading, never silently auto-healed."""

from dataclasses import replace

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerPosition, BrokerTrade
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.portfolio.reconciliation import (
    DiscrepancyKind,
    Reconciler,
    ReconciliationComparator,
    ReconciliationSnapshot,
)
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

    async def snapshot(self):
        if self.error:
            raise OSError("offline")
        return self.data

    async def record(self, record):
        self.records.append(record)

    async def halt(self, reason):
        self.halts.append(reason)


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

    async def test_run_persists_clean_and_halts_on_mismatch_or_missing_evidence(self):
        rig = ReconciliationRig()
        service = Reconciler(
            rig,
            ReconciliationComparator(),
            rig,
            rig,
            FixedClock(NOW),
            IdGenerator(),
            RecordingAlertSink(),
        )
        assert await service.run() == ()
        assert rig.records[-1].status == "CLEAN" and rig.halts == []
        rig.data = replace(rig.data, broker_orders=())
        assert await service.run()
        assert rig.records[-1].status == "FAILED" and len(rig.halts) == 1
        rig.error = True
        with pytest.raises(OSError):
            await service.run()
        assert len(rig.halts) == 2

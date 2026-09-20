"""The scheduler reprices only what is due, from persisted state alone, and stops at the bound."""

from datetime import timedelta
from decimal import Decimal

from emporos.domain.money import Money
from emporos.execution.reprice_scheduler import RepriceScheduler
from emporos.execution.repricing import RepriceCoordinator, RepricePolicy
from emporos.persistence.records import OrderRecord
from emporos.session.replacement import RiskReplacementReviewer
from tests.support.fakes import RecordingAlertSink
from tests.unit.execution.rig import ExecutionRig

POLICY = RepricePolicy(timedelta(seconds=5), 2, Decimal("100"))


class Quoter:
    def __init__(self, price: str | None) -> None:
        self.price = price

    async def candidate(self, order: OrderRecord) -> Money | None:
        return None if self.price is None else Money.of(self.price)


def build(rig: ExecutionRig, quoter: Quoter) -> tuple[RepriceScheduler, RecordingAlertSink]:
    alerts = RecordingAlertSink()
    coordinator = RepriceCoordinator(
        rig.engine, RiskReplacementReviewer(rig.risk, rig.clock), POLICY, rig.clock, rig.sync
    )
    return RepriceScheduler(rig.journal, coordinator, POLICY, quoter, rig.clock, alerts), alerts


async def test_an_order_is_repriced_only_once_it_has_rested_long_enough() -> None:
    rig = ExecutionRig()
    scheduler, _ = build(rig, Quoter("100.05"))
    first = await rig.engine.place(await rig.approval())
    assert await scheduler.run_once() == []  # too young
    rig.wait(6)
    (outcome,) = await scheduler.run_once()
    assert outcome.order_id == first.id and outcome.result != first.id
    assert rig.journal.orders[first.id].state == "CANCELLED"
    (child,) = (o for o in rig.journal.orders.values() if o.parent_order_id == first.id)
    assert child.limit_price == Money.of("100.05") and child.state == "OPEN"


async def test_the_chain_stops_at_the_bound_and_is_reported_once() -> None:
    rig = ExecutionRig()
    scheduler, alerts = build(rig, Quoter("100.05"))
    await rig.engine.place(await rig.approval())
    for _ in range(3):  # two reprices are allowed; the third pass finds nothing due
        rig.wait(6)
        await scheduler.run_once()
    live = [o for o in rig.journal.orders.values() if o.state == "OPEN"]
    assert len(live) == 1 and live[0].reprice_count == 2
    rig.wait(6)
    assert await scheduler.run_once() == []
    assert len(rig.gateway.placements) == 3
    assert alerts.alerts == []


async def test_a_price_beyond_the_chase_bound_is_refused_and_alerted_once() -> None:
    rig = ExecutionRig()
    scheduler, alerts = build(rig, Quoter("110"))  # 1000 bps away; the bound is 100
    order = await rig.engine.place(await rig.approval())
    rig.wait(6)
    for _ in range(3):
        (outcome,) = await scheduler.run_once()
        assert "chase" in outcome.result
    assert rig.journal.orders[order.id].state == "OPEN"  # untouched
    assert [name for name, _ in alerts.alerts] == ["reprice_refused"]


async def test_no_market_means_no_reprice() -> None:
    rig = ExecutionRig()
    scheduler, _ = build(rig, Quoter(None))
    await rig.engine.place(await rig.approval())
    rig.wait(60)
    assert await scheduler.run_once() == []
    assert len(rig.gateway.placements) == 1


async def test_only_resting_signal_orders_are_touched() -> None:
    rig = ExecutionRig()
    scheduler, _ = build(rig, Quoter("100.05"))
    order = await rig.engine.place(await rig.approval())
    await rig.engine.cancel(order.id)
    rig.wait(60)
    assert await scheduler.run_once() == []  # cancelled: nothing to chase

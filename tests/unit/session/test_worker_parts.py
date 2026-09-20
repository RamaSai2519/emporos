"""The small parts the worker is built from, each against doubles for its own collaborators."""

from datetime import time, timedelta
from decimal import Decimal

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerOrderUpdate
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus
from emporos.domain.orders import OrderSide, OrderType
from emporos.execution.fills import SyncResult
from emporos.execution.order_updates import OrderUpdateTranslator
from emporos.portfolio.reconciliation import Discrepancy, DiscrepancyKind, ReconciliationTrigger
from emporos.portfolio.snapshots import SnapshotKind
from emporos.session.bar_feed import ClosedBarQueue
from emporos.session.close_out import EndOfDay
from emporos.session.host import ManagedRun, StrategyHost
from emporos.session.recovery import StartupRecovery
from emporos.session.risk_facts import (
    JournalOrderFlow,
    LedgerAccountFacts,
    QuotedMarketFacts,
    VenueHealth,
)
from emporos.session.updates import OrderUpdateRouter
from emporos.session.worker import SessionSchedule
from tests.support.fakes import RecordingAlertSink
from tests.support.records import NOW, RecordFactory


class TestSessionSchedule:
    def test_times_are_ist_wall_clock_on_the_days_date(self) -> None:
        schedule = SessionSchedule()
        square = schedule.square_off(NOW)  # NOW is 09:30 IST on 2026-01-05
        assert square.isoformat() == "2026-01-05T15:15:00+05:30"
        assert schedule.close(NOW).isoformat() == "2026-01-05T15:30:00+05:30"

    def test_it_must_be_ordered_and_positive(self) -> None:
        with pytest.raises(ValueError, match="before the close"):
            SessionSchedule(square_off_at=time(15, 30), close_at=time(15, 15))
        with pytest.raises(ValueError, match="positive"):
            SessionSchedule(poll_interval=timedelta(0))


class TestBarQueue:
    def bar(self, timeframe: Timeframe) -> Candle:
        p = Money.of("100")
        return Candle("NSE:1", timeframe, NOW, p, p, p, p, 10)

    def test_only_the_wanted_timeframes_are_kept_and_draining_empties_it(self) -> None:
        queue = ClosedBarQueue(frozenset({Timeframe.M5}))
        queue.on_candle(self.bar(Timeframe.M1))
        queue.on_candle(self.bar(Timeframe.M5))
        assert [c.timeframe for c in queue.drain()] == [Timeframe.M5]  # type: ignore[union-attr]
        assert queue.drain() == []


class TestUpdateRouter:
    def order(self) -> BrokerOrder:
        return BrokerOrder(
            "b1", "TAG", "NSE:1", OrderSide.BUY, OrderType.LIMIT, 10, 0, BrokerOrderStatus.OPEN
        )

    def test_updates_queue_in_order_and_the_dirty_cue_fires_once_per_burst(self) -> None:
        router = OrderUpdateRouter()
        assert not router.take_dirty()
        for _ in range(3):
            router.on_update(BrokerOrderUpdate(self.order(), NOW))
        assert router.take_dirty() and not router.take_dirty()
        assert len(router.drain()) == 3 and router.drain() == []

    def test_the_queue_is_bounded(self) -> None:
        router = OrderUpdateRouter(max_queued=2)
        for _ in range(5):
            router.on_update(BrokerOrderUpdate(self.order(), NOW))
        assert len(router.drain()) == 2


class Runner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def handle(self, event: object) -> None:
        self.calls.append(f"event:{event}")

    async def handle_order_update(self, update: OrderUpdate) -> None:
        self.calls.append(f"update:{update.status.value}")

    async def end_session(self) -> None:
        self.calls.append("end")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


class Events:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    def drain(self) -> list[object]:
        out, self.events = self.events, []
        return out


class Orders:
    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping

    async def by_broker_order_id(self, broker_order_id: str):  # type: ignore[no-untyped-def]
        run = self.mapping.get(broker_order_id, "missing")
        if run == "missing":
            return None
        return RecordFactory().order(strategy_run_id=run)


class TestStrategyHost:
    async def test_bars_reach_every_run_and_an_update_reaches_only_its_own(self) -> None:
        a, b, router = Runner(), Runner(), OrderUpdateRouter()
        host = StrategyHost(
            [ManagedRun("run-a", a), ManagedRun("run-b", b)],  # type: ignore[list-item]
            Events(["bar1"]),  # type: ignore[arg-type]
            router,
            Orders({"b1": "run-b", "b2": None}),  # type: ignore[arg-type]
            OrderUpdateTranslator(),
        )
        await host.start()
        order = TestUpdateRouter().order()
        router.on_update(BrokerOrderUpdate(order, NOW))
        router.on_update(
            BrokerOrderUpdate(
                BrokerOrder(  # a manual order: no strategy's business
                    "b2", "T", "NSE:1", OrderSide.BUY, OrderType.LIMIT, 1, 0, BrokerOrderStatus.OPEN
                ),
                NOW,
            )
        )
        router.on_update(
            BrokerOrderUpdate(
                BrokerOrder(  # an order we never heard of
                    "b9", "T", "NSE:1", OrderSide.BUY, OrderType.LIMIT, 1, 0, BrokerOrderStatus.OPEN
                ),
                NOW,
            )
        )
        await host.pump()
        await host.end_session()
        await host.shutdown()
        assert a.calls == ["start", "event:bar1", "end", "shutdown"]
        working = f"update:{OrderUpdateStatus.WORKING.value}"
        assert b.calls == ["start", working, "event:bar1", "end", "shutdown"]


class Recovery:
    def __init__(self, error: Exception | None = None, found: int = 0) -> None:
        self.error, self.found = error, found
        self.calls: list[str] = []

    async def recover(self) -> tuple[object, ...]:
        self.calls.append("recover")
        if self.error:
            raise self.error
        return (1, 2)

    async def sync(self) -> SyncResult:
        self.calls.append("sync")
        return SyncResult(3, 0, 0, 0)

    async def run(self, trigger: ReconciliationTrigger) -> tuple[Discrepancy, ...]:
        self.calls.append(f"reconcile:{trigger.value}")
        return tuple(
            Discrepancy(DiscrepancyKind.EXTERNAL_ORDER, "x", "y") for _ in range(self.found)
        )


class TestStartupRecovery:
    async def test_it_resolves_then_applies_fills_then_reconciles_in_that_order(self) -> None:
        r = Recovery()
        result = await StartupRecovery(r, r, r, RecordingAlertSink()).run()  # type: ignore[arg-type]
        assert r.calls == ["recover", "sync", "reconcile:STARTUP"]
        assert (result.clean, result.orders_checked, result.fills_applied) == (True, 2, 3)

    async def test_a_discrepancy_or_an_error_is_not_clean(self) -> None:
        dirty = Recovery(found=2)
        result = await StartupRecovery(dirty, dirty, dirty, RecordingAlertSink()).run()  # type: ignore[arg-type]
        assert not result.clean and "2 reconciliation discrepancies" in result.problem
        alerts = RecordingAlertSink()
        broken = Recovery(error=OSError("down"))
        result = await StartupRecovery(broken, broken, broken, alerts).run()  # type: ignore[arg-type]
        assert not result.clean and "OSError" in result.problem
        assert [n for n, _ in alerts.alerts] == ["recovery_failed"]
        assert broken.calls == ["recover"]  # nothing after the failure ran


class TestEndOfDay:
    async def test_it_syncs_resolves_reconciles_and_snapshots(self) -> None:
        r = Recovery(found=1)
        snapshots: list[SnapshotKind] = []

        class Snap:
            async def take(self, kind: SnapshotKind):  # type: ignore[no-untyped-def]
                snapshots.append(kind)
                return RecordFactory().portfolio_snapshot()

            async def resolve_unresolved(self) -> tuple[object, ...]:
                r.calls.append("resolve")
                return ()

        snap = Snap()
        out = await EndOfDay(r, snap, r, snap, RecordingAlertSink()).close_out()  # type: ignore[arg-type]
        assert r.calls == ["sync", "resolve", "reconcile:EOD"] and snapshots == [SnapshotKind.EOD]
        assert out.discrepancies == 1 and out.snapshot is not None

    async def test_a_failed_snapshot_is_alerted_and_does_not_lose_the_reconciliation(self) -> None:
        r, alerts = Recovery(), RecordingAlertSink()

        class Broken:
            async def take(self, kind: SnapshotKind):  # type: ignore[no-untyped-def]
                raise OSError("down")

            async def resolve_unresolved(self) -> tuple[object, ...]:
                return ()

        broken = Broken()
        out = await EndOfDay(r, broken, r, broken, alerts).close_out()  # type: ignore[arg-type]
        assert out.snapshot is None and out.realised_pnl is None and out.trades is None
        assert [n for n, _ in alerts.alerts] == ["eod_snapshot_failed"]


class TestRiskFacts:
    async def test_market_facts_use_the_quote_and_mark_staleness_and_fail_toward_stale(
        self,
    ) -> None:
        from emporos.broker.models import Quote

        class Quotes:
            def __init__(self, quotes: list[Quote] | Exception) -> None:
                self.quotes = quotes

            async def get_quote(self, ids):  # type: ignore[no-untyped-def]
                if isinstance(self.quotes, Exception):
                    raise self.quotes
                return self.quotes

        class Marks:
            def __init__(self, fresh: bool) -> None:
                self.fresh = fresh

            def marks(self) -> dict[str, Money]:
                return {"A": Money.of("100")} if self.fresh else {}

        p = Money.of("100")
        quote = Quote("A", p, p, p, p, p, bid=Money.of("99.95"), ask=Money.of("100.05"),
                      lower_circuit=Money.of("90"), upper_circuit=Money.of("110"))  # fmt: skip
        market = await QuotedMarketFacts(Quotes([quote]), Marks(True)).market_facts("A")  # type: ignore[arg-type]
        assert (market.bid, market.ask, market.stale) == (
            Money.of("99.95"),
            Money.of("100.05"),
            False,
        )
        assert (await QuotedMarketFacts(Quotes([quote]), Marks(False)).market_facts("A")).stale  # type: ignore[arg-type]
        assert (await QuotedMarketFacts(Quotes([]), Marks(True)).market_facts("A")).stale  # type: ignore[arg-type]
        assert (await QuotedMarketFacts(Quotes(OSError()), Marks(True)).market_facts("A")).stale  # type: ignore[arg-type]

    async def test_order_flow_lists_working_orders_and_recent_send_times(self) -> None:
        f = RecordFactory()
        working = f.order(state="OPEN", instrument_id="A", side=OrderSide.SELL, created_at=NOW)
        done = f.order(state="FILLED", created_at=NOW)

        class Journal:
            async def active(self) -> list:  # type: ignore[type-arg]
                return [working, done]

            async def orders_created_since(self, since):  # type: ignore[no-untyped-def]
                assert since == NOW - timedelta(minutes=1)
                return [working, done]

        flow = await JournalOrderFlow(Journal(), Journal(), FixedClock(NOW)).order_flow()  # type: ignore[arg-type]
        assert [(w.instrument_id, w.side) for w in flow.working] == [("A", OrderSide.SELL)]
        assert flow.recent_order_times == (NOW, NOW)

    def test_venue_health_starts_unhealthy(self) -> None:
        health = VenueHealth()
        assert not health.session_ok() and not health.order_feed_ok()
        health.set(session=True, feed=False)
        assert health.session_ok() and not health.order_feed_ok()

    async def test_account_facts_combine_positions_pnl_and_strategy_pnl(self) -> None:
        from emporos.portfolio.ledger import PortfolioValuator, PositionCalculator
        from emporos.portfolio.service import PortfolioService
        from emporos.session.strategy_positions import StrategyPositionBook

        f = RecordFactory()

        class Positions:
            async def all_positions(self) -> list:  # type: ignore[type-arg]
                return [
                    f.position(
                        instrument_id="A",
                        net_quantity=10,
                        average_price=Money.of("100"),
                        realised_pnl=Money.of("5"),
                    )
                ]

            async def open_positions(self) -> list:  # type: ignore[type-arg]
                return await self.all_positions()

        class Marks:
            def marks(self) -> dict[str, Money]:
                return {"A": Money.of("102")}

        book = StrategyPositionBook("acct", PositionCalculator())
        order = f.order(_id="o1", strategy_run_id="run-a", instrument_id="A")
        book.on_fill(order, f.execution(order_id="o1", instrument_id="A", quantity=10,
                                        price=Money.of("100"), account_id="acct"))  # fmt: skip
        source = LedgerAccountFacts(
            PortfolioService(Positions(), Marks(), PortfolioValuator()), book, Marks()
        )
        facts = await source.account_facts(None)  # type: ignore[arg-type]
        assert facts.daily_pnl == Money.of("25")  # 5 realised + 10 * (102 - 100)
        assert facts.position("A").net_quantity == 10
        assert facts.strategy_pnl == {"run-a": Money.of("20")} and Decimal(1)

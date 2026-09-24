"""The anomaly tripwire (EM-189): each detector on its own, then the tripwire that halts on them."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerOrderUpdate
from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.persistence.records import OrderRecord
from emporos.session.rejection_tracker import RejectionTracker
from emporos.session.tripwire import (
    Anomaly,
    AnomalyTripwire,
    FeedDroppedDetector,
    OrderUpdateGapDetector,
    RejectionBurstDetector,
    UnresolvedUnknownOrderDetector,
    WidespreadStalenessDetector,
)
from emporos.session.tripwire_config import TripwireSettingsLoader
from tests.support.fakes import RecordingAlertSink
from tests.support.records import RecordFactory
from tests.support.risk import NOW

MINUTE = timedelta(minutes=1)


class FakeFeed:
    def __init__(self, down_since: datetime | None = None, watched: int = 0, stale: int = 0):
        self.down_since, self.watched, self.stale = down_since, watched, stale

    def feed_down_since(self) -> datetime | None:
        return self.down_since

    def watched_count(self) -> int:
        return self.watched

    def stale_count(self) -> int:
        return self.stale


class FakeOrders:
    def __init__(self, *orders: OrderRecord) -> None:
        self.orders = list(orders)

    async def active(self) -> list[OrderRecord]:
        return self.orders


class FakeOrderFeed:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def order_feed_ok(self) -> bool:
        return self.ok


def order(state: str, updated_ago: timedelta = timedelta(0)) -> OrderRecord:
    return RecordFactory().order(state=state, updated_at=NOW - updated_ago)


class TestFeedDropped:
    async def test_a_connected_feed_is_fine(self) -> None:
        assert await FeedDroppedDetector(FakeFeed(), MINUTE).check(NOW) is None

    async def test_a_drop_within_the_allowance_is_tolerated_and_beyond_it_is_an_anomaly(
        self,
    ) -> None:
        detector = FeedDroppedDetector(FakeFeed(down_since=NOW - MINUTE), MINUTE)
        assert await detector.check(NOW) is None  # exactly the allowance
        anomaly = await detector.check(NOW + timedelta(seconds=1))
        assert anomaly is not None and anomaly.detector == "feed_dropped"

    def test_the_allowance_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            FeedDroppedDetector(FakeFeed(), timedelta(0))


class TestWidespreadStaleness:
    async def test_nothing_watched_is_not_an_anomaly(self) -> None:
        assert await WidespreadStalenessDetector(FakeFeed(), Decimal("0.5")).check(NOW) is None

    async def test_exactly_the_fraction_is_tolerated_and_more_is_not(self) -> None:
        detector = WidespreadStalenessDetector(FakeFeed(watched=4, stale=2), Decimal("0.5"))
        assert await detector.check(NOW) is None
        over = WidespreadStalenessDetector(FakeFeed(watched=4, stale=3), Decimal("0.5"))
        anomaly = await over.check(NOW)
        assert anomaly is not None and "3 of 4" in anomaly.reason

    @pytest.mark.parametrize("fraction", [Decimal(0), Decimal(1)])
    def test_the_fraction_must_be_strictly_between_zero_and_one(self, fraction: Decimal) -> None:
        with pytest.raises(ValueError, match="between"):
            WidespreadStalenessDetector(FakeFeed(), fraction)


class TestUnknownOrders:
    LIMIT = timedelta(seconds=90)

    async def test_a_recent_unknown_order_is_tolerated(self) -> None:
        detector = UnresolvedUnknownOrderDetector(FakeOrders(order("UNKNOWN")), self.LIMIT)
        assert await detector.check(NOW) is None

    async def test_an_unknown_order_beyond_the_limit_is_an_anomaly_naming_it(self) -> None:
        stuck = order("UNKNOWN", updated_ago=timedelta(seconds=91))
        anomaly = await UnresolvedUnknownOrderDetector(FakeOrders(stuck), self.LIMIT).check(NOW)
        assert anomaly is not None and stuck.id in anomaly.reason

    async def test_an_old_order_that_is_merely_open_is_not_unknown(self) -> None:
        old_open = order("OPEN", updated_ago=timedelta(hours=1))
        assert (
            await UnresolvedUnknownOrderDetector(FakeOrders(old_open), self.LIMIT).check(NOW)
            is None
        )


def rejected(at: datetime) -> BrokerOrderUpdate:
    broker_order = BrokerOrder(
        "B1", "tag", "NSE:1", OrderSide.BUY, None, 10, 0, BrokerOrderStatus.REJECTED,
        Money.of("100"),
    )  # fmt: skip
    return BrokerOrderUpdate(broker_order, at)


class TestRejectionBurst:
    async def test_a_burst_inside_the_window_is_an_anomaly_and_a_spread_out_one_is_not(
        self,
    ) -> None:
        tracker = RejectionTracker()
        for seconds in (50, 40, 30, 20):
            tracker.on_update(rejected(NOW - timedelta(seconds=seconds)))
        detector = RejectionBurstDetector(tracker, 5, MINUTE)
        assert await detector.check(NOW) is None
        tracker.on_update(rejected(NOW))
        anomaly = await detector.check(NOW)
        assert anomaly is not None and "5 broker rejections" in anomaly.reason
        assert await detector.check(NOW + timedelta(seconds=45)) is None  # they age out

    def test_only_rejections_are_counted_and_the_history_is_bounded(self) -> None:
        tracker = RejectionTracker(capacity=2)
        filled = BrokerOrder(
            "B1", "tag", "NSE:1", OrderSide.BUY, None, 10, 10, BrokerOrderStatus.FILLED
        )
        tracker.on_update(BrokerOrderUpdate(filled, NOW))
        for _ in range(3):
            tracker.on_update(rejected(NOW))
        assert tracker.rejections_since(NOW - MINUTE) == 2
        with pytest.raises(ValueError, match="room"):
            RejectionTracker(capacity=0)

    def test_the_count_and_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="count"):
            RejectionBurstDetector(RejectionTracker(), 0, MINUTE)
        with pytest.raises(ValueError, match="window"):
            RejectionBurstDetector(RejectionTracker(), 1, timedelta(0))


class TestOrderUpdateGap:
    GAP = timedelta(seconds=30)

    async def test_a_healthy_order_feed_never_trips(self) -> None:
        detector = OrderUpdateGapDetector(FakeOrderFeed(True), FakeOrders(order("OPEN")), self.GAP)
        assert await detector.check(NOW + MINUTE) is None

    async def test_a_dead_feed_with_no_open_orders_is_not_an_anomaly(self) -> None:
        detector = OrderUpdateGapDetector(FakeOrderFeed(False), FakeOrders(), self.GAP)
        assert await detector.check(NOW) is None
        assert await detector.check(NOW + MINUTE) is None

    async def test_a_feed_dead_beyond_the_gap_with_orders_open_is_an_anomaly(self) -> None:
        feed = FakeOrderFeed(False)
        detector = OrderUpdateGapDetector(feed, FakeOrders(order("OPEN")), self.GAP)
        assert await detector.check(NOW) is None  # the gap starts now
        assert await detector.check(NOW + self.GAP) is None
        assert await detector.check(NOW + self.GAP + timedelta(seconds=1)) is not None

    async def test_recovery_restarts_the_gap(self) -> None:
        feed = FakeOrderFeed(False)
        detector = OrderUpdateGapDetector(feed, FakeOrders(order("OPEN")), self.GAP)
        await detector.check(NOW)
        feed.ok = True
        await detector.check(NOW + timedelta(seconds=20))
        feed.ok = False
        assert await detector.check(NOW + timedelta(seconds=40)) is None  # a fresh gap


class ScriptedDetector:
    def __init__(self, name: str, anomaly: Anomaly | None = None, error: bool = False) -> None:
        self.name, self.anomaly, self.error, self.calls = name, anomaly, error, 0

    async def check(self, now: datetime) -> Anomaly | None:
        self.calls += 1
        if self.error:
            raise RuntimeError("boom")
        return self.anomaly


class RecordingHalt:
    def __init__(self, error: Exception | None = None) -> None:
        self.reasons: list[str] = []
        self.error = error

    async def halt(self, reason: str) -> None:
        self.reasons.append(reason)
        if self.error is not None:
            raise self.error


class ScriptedSwitch:
    def __init__(self) -> None:
        self.is_halted = False

    async def halted(self) -> bool:
        return self.is_halted


class TestTripwire:
    def build(self, *detectors: ScriptedDetector):  # type: ignore[no-untyped-def]
        halt, switch, alerts = RecordingHalt(), ScriptedSwitch(), RecordingAlertSink()
        tripwire = AnomalyTripwire(detectors, halt, switch, FixedClock(NOW), alerts)
        return tripwire, halt, switch, alerts

    def test_a_tripwire_with_no_detector_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no detector"):
            AnomalyTripwire(
                [], RecordingHalt(), ScriptedSwitch(), FixedClock(NOW), RecordingAlertSink()
            )

    async def test_quiet_detectors_do_nothing(self) -> None:
        tripwire, halt, _, alerts = self.build(ScriptedDetector("a"), ScriptedDetector("b"))
        assert await tripwire.run_once() is None
        assert halt.reasons == [] and alerts.alerts == []

    async def test_the_first_anomaly_halts_once_and_alerts(self) -> None:
        later = ScriptedDetector("later", Anomaly("later", "second"))
        tripwire, halt, _, alerts = self.build(
            ScriptedDetector("quiet"), ScriptedDetector("bad", Anomaly("bad", "it broke")), later
        )
        anomaly = await tripwire.run_once()
        assert anomaly == Anomaly("bad", "it broke")
        assert halt.reasons == ["tripwire bad: it broke"]
        assert alerts.alerts == [("tripwire_halt", "tripwire bad: it broke")]
        assert later.calls == 0  # the first anomaly ends the pass

    async def test_it_does_not_halt_again_while_the_switch_is_still_being_set(self) -> None:
        tripwire, halt, switch, _ = self.build(ScriptedDetector("bad", Anomaly("bad", "x")))
        await tripwire.run_once()
        await tripwire.run_once()  # the monitor has not seen the halt yet
        switch.is_halted = True
        await tripwire.run_once()
        assert len(halt.reasons) == 1

    async def test_it_re_arms_only_after_an_operator_resume_and_then_halts_again(self) -> None:
        detector = ScriptedDetector("bad", Anomaly("bad", "x"))
        tripwire, halt, switch, _ = self.build(detector)
        await tripwire.run_once()
        switch.is_halted = True
        await tripwire.run_once()  # sees the halt
        switch.is_halted = False  # `emporos resume`
        await tripwire.run_once()  # re-arms
        await tripwire.run_once()
        assert len(halt.reasons) == 2

    async def test_a_detector_that_cannot_answer_fails_closed(self) -> None:
        tripwire, halt, _, _ = self.build(ScriptedDetector("broken", error=True))
        anomaly = await tripwire.run_once()
        assert anomaly is not None and "detector failed" in anomaly.reason
        assert len(halt.reasons) == 1

    async def test_a_halt_that_cannot_be_set_is_never_swallowed(self) -> None:
        halt = RecordingHalt(RuntimeError("could not engage the kill switch"))
        tripwire = AnomalyTripwire(
            [ScriptedDetector("bad", Anomaly("bad", "x"))],
            halt, ScriptedSwitch(), FixedClock(NOW), RecordingAlertSink(),
        )  # fmt: skip
        with pytest.raises(RuntimeError, match="could not engage"):
            await tripwire.run_once()


VALID = {
    "poll_seconds": 2, "feed_drop_halt_seconds": 60, "stale_instrument_fraction": "0.5",
    "unknown_order_seconds": 90, "rejection_burst_count": 5, "rejection_burst_seconds": 60,
    "order_feed_down_seconds": 30,
}  # fmt: skip


class TestSettings:
    def test_the_shipped_settings_load_in_every_profile(self) -> None:
        from emporos.core.config import YamlConfigLoader

        for profile in ("local", "staging", "production"):
            settings = TripwireSettingsLoader(YamlConfigLoader().load(profile)).load()
            assert settings.feed_drop_halt == timedelta(seconds=60)
            assert settings.stale_fraction == Decimal("0.5")

    def test_every_accessor_reflects_its_setting(self) -> None:
        s = TripwireSettingsLoader({"tripwire": VALID}).load()
        assert (s.poll, s.unknown_order, s.rejection_window, s.order_feed_down) == (
            timedelta(seconds=2), timedelta(seconds=90), timedelta(seconds=60),
            timedelta(seconds=30),
        )  # fmt: skip

    def test_a_missing_section_a_float_a_typo_and_an_out_of_range_value_are_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="no `tripwire:`"):
            TripwireSettingsLoader({}).load()
        with pytest.raises(ConfigurationError, match="stale_instrument_fraction"):
            TripwireSettingsLoader({"tripwire": {**VALID, "stale_instrument_fraction": 0.5}}).load()
        with pytest.raises(ConfigurationError, match="poll_secs"):
            TripwireSettingsLoader({"tripwire": {**VALID, "poll_secs": 1}}).load()
        with pytest.raises(ConfigurationError, match="stale_instrument_fraction"):
            TripwireSettingsLoader({"tripwire": {**VALID, "stale_instrument_fraction": "1"}}).load()

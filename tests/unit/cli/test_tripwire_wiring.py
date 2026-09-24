"""The worker composition wires the tripwire for paper and live alike (EM-189)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from emporos.cli.worker_composition import _tripwire_jobs
from emporos.core.clock import FixedClock
from emporos.session.risk_facts import VenueHealth
from emporos.session.tripwire_config import TripwireSettings
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import NOW

SETTINGS = TripwireSettings(
    poll_seconds=2, feed_drop_halt_seconds=60, stale_instrument_fraction="0.5",  # type: ignore[arg-type]
    unknown_order_seconds=90, rejection_burst_count=5, rejection_burst_seconds=60,
    order_feed_down_seconds=30,
)  # fmt: skip


class Broker:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def on_order_update(self, handler: Any) -> None:
        self.handlers.append(handler)


class Feed:
    def feed_down_since(self) -> None:
        return None

    def watched_count(self) -> int:
        return 0

    def stale_count(self) -> int:
        return 0


class Orders:
    async def active(self) -> list[Any]:
        return []


@dataclass
class Common:
    tripwire: TripwireSettings | None
    feed_watch: Feed | None
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))


@dataclass
class Seam:
    health: VenueHealth


@dataclass
class Prelude:
    control: Any
    monitor: Any
    alerts: Any


def build(common: Common, broker: Broker):  # type: ignore[no-untyped-def]
    return _tripwire_jobs(
        common, Seam(VenueHealth()), Prelude(None, None, RecordingAlertSink()), broker, Orders()  # type: ignore[arg-type]
    )  # fmt: skip


def test_without_settings_no_tripwire_is_wired_and_no_handler_is_registered() -> None:
    broker = Broker()
    assert build(Common(None, Feed()), broker) == []
    assert broker.handlers == []


def test_with_settings_it_is_one_polled_job_and_listens_for_rejections() -> None:
    broker = Broker()
    (job,) = build(Common(SETTINGS, Feed()), broker)
    assert job.name == "tripwire" and job.interval == timedelta(seconds=2)
    assert len(broker.handlers) == 1

"""EM-51: ordered synchronous dispatch, subscriber isolation, and the full frame-to-subscriber
chain (reader -> bounded queue -> normalizer -> subscribers)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from emporos.broker.angelone.ws_frames import TickFrameParser, TickFrameReader
from emporos.core.clock import FixedClock
from emporos.domain.ticks import Tick
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.pipeline import TickPipeline
from emporos.marketdata.queue import BoundedTickQueue
from tests.support.fakes import RecordingAlertSink, make_instrument
from tests.support.frames import FrameBuilder, epoch_ms

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)  # 10:00 IST


class Recorder:
    def __init__(self, log: list[str], name: str) -> None:
        self._log, self._name = log, name
        self.ticks: list[Tick] = []

    def on_tick(self, tick: Tick) -> None:
        self.ticks.append(tick)
        self._log.append(f"{self._name}:{tick.sequence}")


class Exploding:
    def on_tick(self, tick: Tick) -> None:
        raise RuntimeError("strategy bug")


class Rig:
    def __init__(self, *subscribers: object, alerts: RecordingAlertSink | None = None) -> None:
        clock = FixedClock(T0)
        self.queue = BoundedTickQueue(clock)
        self.alerts = alerts
        self.pipeline = TickPipeline(
            self.queue,
            TickNormalizer(InstrumentCache([make_instrument("3045")])),
            subscribers,  # type: ignore[arg-type]
            alerts,
        )
        self.reader = TickFrameReader(TickFrameParser(), self.queue)

    def frame(self, seq: int, at: datetime, ltp_paise: int = 99620, token: str = "3045") -> None:
        self.reader.on_frame(
            FrameBuilder.quote(token=token, sequence=seq, ts_ms=epoch_ms(at), ltp_paise=ltp_paise)
        )


def test_dispatch_is_total_ordered_every_subscriber_sees_a_tick_before_the_next() -> None:
    log: list[str] = []
    rig = Rig(Recorder(log, "a"), Recorder(log, "b"))

    for n in (1, 2, 3):
        rig.frame(n, T0 + timedelta(seconds=n))
    rig.pipeline.drain()

    assert log == ["a:1", "b:1", "a:2", "b:2", "a:3", "b:3"]


def test_a_failing_subscriber_is_isolated_alerted_and_does_not_stop_the_others() -> None:
    log: list[str] = []
    alerts = RecordingAlertSink()
    healthy = Recorder(log, "ok")
    rig = Rig(Exploding(), healthy, alerts=alerts)

    for n in (1, 2):
        rig.frame(n, T0 + timedelta(seconds=n))
    rig.pipeline.drain()

    assert log == ["ok:1", "ok:2"]  # the healthy subscriber saw everything
    assert rig.pipeline.subscriber_errors == 2
    assert [name for name, _ in alerts.alerts] == ["market_data.subscriber_failed"] * 2
    assert "Exploding" in alerts.alerts[0][1] and "NSE:3045" in alerts.alerts[0][1]


def test_dropped_ticks_never_reach_subscribers() -> None:
    log: list[str] = []
    rig = Rig(Recorder(log, "a"))

    rig.frame(1, T0)  # good
    rig.frame(2, T0, token="999999")  # unknown instrument
    rig.frame(3, datetime(2026, 9, 18, 2, 0, tzinfo=UTC))  # 07:30 IST: outside the session
    rig.frame(4, T0)  # a duplicate of tick 1 (same time and price)
    rig.pipeline.drain()

    assert log == ["a:1"]


def test_the_whole_chain_flags_late_ticks_and_still_delivers_them() -> None:
    rig = Rig(rec := Recorder([], "a"))

    rig.frame(1, T0 + timedelta(seconds=10), ltp_paise=99700)
    rig.frame(2, T0 + timedelta(seconds=5), ltp_paise=99650)  # older exchange time
    rig.pipeline.drain()

    assert [(t.sequence, t.out_of_order) for t in rec.ticks] == [(1, False), (2, True)]


async def test_run_consumes_until_cancelled() -> None:
    rec = Recorder([], "a")
    rig = Rig(rec)
    task = asyncio.create_task(rig.pipeline.run())
    try:
        rig.frame(1, T0)
        for _ in range(50):
            if rec.ticks:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert [t.sequence for t in rec.ticks] == [1]

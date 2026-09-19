"""The bounded tick queue: never blocks the reader, drops the OLDEST when full, and says so."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.marketdata.queue import BoundedTickQueue
from tests.support.fakes import RecordingAlertSink, raw_tick

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)


def tick(n: int):  # type: ignore[no-untyped-def]
    return raw_tick(at=T0, sequence=n)


def test_ticks_come_out_in_arrival_order_stamped_when_they_arrived() -> None:
    clock = FixedClock(T0)
    queue = BoundedTickQueue(clock)

    queue.on_raw_tick(tick(1))
    clock.advance(timedelta(seconds=5))
    queue.on_raw_tick(tick(2))
    clock.advance(timedelta(seconds=60))  # sits in the queue a long time before being read

    first, second = queue.get_nowait(), queue.get_nowait()
    assert (first.tick.sequence, second.tick.sequence) == (1, 2)
    assert first.received_ts == T0 and second.received_ts == T0 + timedelta(seconds=5)


def test_when_full_the_oldest_tick_is_dropped_and_the_newest_kept() -> None:
    queue = BoundedTickQueue(FixedClock(T0), max_size=3)

    for n in range(1, 6):
        queue.on_raw_tick(tick(n))  # never blocks, never raises

    assert len(queue) == 3 and queue.dropped == 2
    assert [queue.get_nowait().tick.sequence for _ in range(3)] == [3, 4, 5]


def test_overflow_raises_an_alarm_at_powers_of_ten_not_on_every_drop() -> None:
    alerts = RecordingAlertSink()
    queue = BoundedTickQueue(FixedClock(T0), alerts, max_size=1)

    for n in range(112):
        queue.on_raw_tick(tick(n))  # 111 drops

    assert [name for name, _ in alerts.alerts] == ["market_data.queue_overflow"] * 3  # 1, 10, 100
    assert "100 oldest" in alerts.alerts[-1][1]


async def test_a_consumer_waiting_on_an_empty_queue_is_woken_by_the_next_tick() -> None:
    queue = BoundedTickQueue(FixedClock(T0))
    waiter = asyncio.create_task(queue.get())
    await asyncio.sleep(0)
    assert not waiter.done()

    queue.on_raw_tick(tick(7))

    assert (await asyncio.wait_for(waiter, 1)).tick.sequence == 7


def test_the_queue_must_have_room_for_a_tick() -> None:
    with pytest.raises(ConfigurationError):
        BoundedTickQueue(FixedClock(T0), max_size=0)

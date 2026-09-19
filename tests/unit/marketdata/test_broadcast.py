from __future__ import annotations

from datetime import UTC, datetime

from emporos.domain.ticks import Tick
from emporos.marketdata.broadcast import TickBroadcaster
from tests.support.fakes import make_tick

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)


def test_handlers_run_in_registration_order_for_every_tick() -> None:
    broadcaster, log = TickBroadcaster(), []
    broadcaster.add_handler(lambda t: log.append(("a", t.sequence)))
    broadcaster.add_handler(lambda t: log.append(("b", t.sequence)))

    for seq in (1, 2):
        broadcaster.on_tick(make_tick(T0, sequence=seq))

    assert log == [("a", 1), ("b", 1), ("a", 2), ("b", 2)]


def test_a_failing_handler_is_skipped_and_the_rest_still_run() -> None:
    broadcaster, seen = TickBroadcaster(), []

    def broken(tick: Tick) -> None:
        raise RuntimeError("bug")

    broadcaster.add_handler(broken)
    broadcaster.add_handler(lambda t: seen.append(t.sequence))

    broadcaster.on_tick(make_tick(T0, sequence=9))

    assert seen == [9]


def test_with_no_handlers_a_tick_is_simply_dropped() -> None:
    TickBroadcaster().on_tick(make_tick(T0))

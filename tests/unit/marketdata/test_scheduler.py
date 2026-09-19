"""EM-52: the minute heartbeat closes silent bars on schedule and persists them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from emporos.core.clock import FixedClock
from emporos.domain.candles import Timeframe
from emporos.marketdata.aggregator import MinuteCandleAggregator
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.scheduler import MinuteScheduler
from tests.support.fakes import AdvancingSleeper, InMemoryCandleStore, make_tick

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)  # 10:00 IST
GRACE = timedelta(seconds=1)


class Rig:
    def __init__(self) -> None:
        self.clock = FixedClock(T0 + timedelta(seconds=20))
        self.sleeper = AdvancingSleeper(self.clock)
        self.store = InMemoryCandleStore()
        self.aggregator = MinuteCandleAggregator(self.clock, grace=GRACE)
        self.persister = CandlePersister(self.store)
        self.aggregator.subscribe(self.persister)
        self.scheduler = MinuteScheduler(
            self.aggregator, self.persister, self.clock, self.sleeper, GRACE
        )

    async def stored(self) -> list[str]:
        bars = await self.store.read("NSE:3045", Timeframe.M1, T0, T0 + timedelta(hours=1))
        return [b.ts.strftime("%M") for b in bars]


async def test_it_sleeps_to_the_next_boundary_plus_grace_then_closes_and_flushes() -> None:
    rig = Rig()
    rig.aggregator.on_tick(make_tick(T0 + timedelta(seconds=10), "100.00"))

    await rig.scheduler.run_once()

    assert rig.sleeper.sleeps == [41.0]  # 10:00:20 -> 10:01:01
    assert rig.clock.now() == T0 + timedelta(minutes=1, seconds=1)
    assert await rig.stored() == ["30"]  # the 10:00 bar is closed AND persisted


async def test_a_silent_instrument_keeps_getting_a_persisted_bar_every_minute() -> None:
    rig = Rig()
    rig.aggregator.on_tick(make_tick(T0 + timedelta(seconds=10), "100.00"))

    for _ in range(4):
        await rig.scheduler.run_once()  # no further ticks at all

    assert await rig.stored() == ["30", "31", "32", "33"]
    assert rig.sleeper.sleeps[1:] == [60.0, 60.0, 60.0]  # exactly one minute apart thereafter

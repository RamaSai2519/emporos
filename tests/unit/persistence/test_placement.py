"""EM-57: age-based tier placement for candle writes; reads stay identical whichever tier serves."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candles import CandleRepository
from emporos.persistence.placement import RetentionPlacement
from tests.support.fakes import InMemoryCandleStore, InMemoryObjectStore

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)

# A long hot tier (the old defaults): these tests exercise WHERE a bar goes, at ages that only make
# sense with these boundaries. The shipped defaults are pinned by one test of their own.
LONG_HOT: dict[Timeframe, int | None] = {
    Timeframe.M1: 90,
    Timeframe.M5: 365,
    Timeframe.M15: 365,
    Timeframe.H1: 365,
    Timeframe.D1: None,
}
INST = "NSE:3045"


def bar(age_days: float, timeframe: Timeframe = Timeframe.M1, price: str = "100.00") -> Candle:
    p = Money.of(price)
    ts = (NOW - timedelta(days=age_days)).replace(second=0, microsecond=0)
    return Candle(INST, timeframe, ts, p, p, p, p, 10)


class Rig:
    def __init__(self, placement: RetentionPlacement | None = None) -> None:
        self.hot = InMemoryCandleStore()
        self.cold = ParquetCandleArchive(InMemoryObjectStore())
        self.repo = CandleRepository(self.hot, self.cold, placement)
        self.placement = RetentionPlacement(FixedClock(NOW), LONG_HOT)

    async def tiers(self, timeframe: Timeframe) -> tuple[list[datetime], list[datetime]]:
        start, end = NOW - timedelta(days=800), NOW + timedelta(days=1)
        hot = await self.hot.read(INST, timeframe, start, end)
        cold = await self.cold.read(INST, timeframe, start, end)
        return [b.ts for b in hot], [b.ts for b in cold]


def test_the_default_hot_tier_holds_only_what_the_live_pipeline_needs() -> None:
    p = RetentionPlacement(FixedClock(NOW))  # DEFAULTS
    assert p.hot_cutoff(Timeframe.M1) == NOW - timedelta(days=3)
    assert p.hot_cutoff(Timeframe.M5) == p.hot_cutoff(Timeframe.H1) == NOW - timedelta(days=14)
    assert p.hot_cutoff(Timeframe.D1) is None  # daily bars never leave Mongo


def test_retention_is_configurable_and_validated() -> None:
    assert RetentionPlacement(FixedClock(NOW), {Timeframe.M1: 30}).hot_cutoff(Timeframe.M1) == (
        NOW - timedelta(days=30)
    )
    with pytest.raises(ConfigurationError):
        RetentionPlacement(FixedClock(NOW), {Timeframe.M1: 0})


async def test_recent_bars_go_hot_and_old_bars_go_straight_to_the_archive() -> None:
    rig = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT))
    recent, old = bar(10), bar(200)

    await rig.repo.upsert([recent, old])

    hot, cold = await rig.tiers(Timeframe.M1)
    assert hot == [recent.ts] and cold == [old.ts]


async def test_the_cutoff_bar_itself_stays_hot() -> None:
    rig = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT))
    cutoff = NOW - timedelta(days=90)
    at_cutoff = bar(90)
    assert at_cutoff.ts >= cutoff.replace(second=0, microsecond=0)

    await rig.repo.upsert([at_cutoff, bar(90.01)])

    hot, cold = await rig.tiers(Timeframe.M1)
    assert at_cutoff.ts in hot and len(cold) == 1


async def test_each_timeframe_is_placed_by_its_own_retention() -> None:
    rig = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT))
    age = 200  # older than 1m's 90 days, younger than 5m's 365, and daily never leaves

    await rig.repo.upsert([bar(age), bar(age, Timeframe.M5), bar(age, Timeframe.D1)])

    assert [len(t) for t in await rig.tiers(Timeframe.M1)] == [0, 1]
    assert [len(t) for t in await rig.tiers(Timeframe.M5)] == [1, 0]
    assert [len(t) for t in await rig.tiers(Timeframe.D1)] == [1, 0]


async def test_without_a_policy_everything_is_hot_as_before() -> None:
    rig = Rig()
    await rig.repo.upsert([bar(10), bar(500)])
    hot, cold = await rig.tiers(Timeframe.M1)
    assert len(hot) == 2 and cold == []


async def test_the_series_read_back_is_identical_whichever_tier_holds_each_bar() -> None:
    bars = [bar(age, price=f"{100 + age / 100:.2f}") for age in (400, 300, 200, 100, 91, 89, 30, 1)]
    placed, all_hot = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT)), Rig()
    await placed.repo.upsert(bars)
    await all_hot.repo.upsert(bars)
    start, end = NOW - timedelta(days=500), NOW + timedelta(days=1)

    from_tiers = await placed.repo.get_range(INST, Timeframe.M1, start, end)

    assert from_tiers == await all_hot.repo.get_range(INST, Timeframe.M1, start, end)
    assert from_tiers == sorted(bars, key=lambda b: b.ts)


async def test_rewriting_the_same_bars_creates_no_duplicates_in_either_tier() -> None:
    rig = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT))
    bars = [bar(200), bar(10)]

    await rig.repo.upsert(bars)
    await rig.repo.upsert(bars)

    assert [len(t) for t in await rig.tiers(Timeframe.M1)] == [1, 1]


async def test_a_batch_that_is_all_one_tier_touches_only_that_tier() -> None:
    rig = Rig(RetentionPlacement(FixedClock(NOW), LONG_HOT))
    await rig.repo.upsert([bar(400), bar(300)])
    hot, cold = await rig.tiers(Timeframe.M1)
    assert hot == [] and len(cold) == 2
    await rig.repo.upsert([])  # an empty write is a no-op

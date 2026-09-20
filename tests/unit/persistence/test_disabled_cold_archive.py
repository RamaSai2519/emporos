"""EM-108: with no bucket configured the cold tier is empty and refuses writes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.persistence.candle_cold import DisabledColdArchive
from emporos.persistence.candles import CandleRepository
from emporos.persistence.placement import RetentionPlacement
from tests.support.fakes import InMemoryCandleStore
from tests.support.strategies import bar_at

NOW = datetime(2026, 9, 20, tzinfo=UTC)


async def test_reading_it_is_an_empty_range_not_an_error() -> None:
    cold = DisabledColdArchive()
    assert await cold.read("NSE:1", Timeframe.M5, NOW - timedelta(days=400), NOW) == []


async def test_writing_to_it_is_refused_loudly() -> None:
    with pytest.raises(ConfigurationError, match="S3_BUCKET"):
        await DisabledColdArchive().archive([bar_at()])


async def test_a_repository_on_it_stores_recent_bars_and_refuses_bars_that_belong_in_cold() -> None:
    hot = InMemoryCandleStore()
    repository = CandleRepository(hot, DisabledColdArchive(), RetentionPlacement(FixedClock(NOW)))
    recent = bar_at(minutes=0)  # 2026-01-05 is older than 5m's 365-day retention? no: 258 days
    ancient = bar_at(minutes=0).__class__(
        "NSE:1001", Timeframe.M5, NOW - timedelta(days=400),
        recent.open, recent.high, recent.low, recent.close, 1,
    )  # fmt: skip

    await repository.upsert([recent])
    with pytest.raises(ConfigurationError):
        await repository.upsert([ancient])

    assert await repository.get_range(
        "NSE:1001", Timeframe.M5, recent.ts, recent.ts + timedelta(minutes=5)
    ) == [recent]

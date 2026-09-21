"""A strategy launched in a live session starts with history, exactly as a replay does: the last
closed bars before now, capped at the depth asked, never a bar that has not closed yet."""

from __future__ import annotations

from datetime import timedelta

from emporos.cli.worker_composition import RepositoryWarmup
from emporos.core.clock import FixedClock
from tests.support.backtest import InMemoryCandles, trading_days
from tests.support.strategies import INSTRUMENT, T0, make_config

CLOSES = ["100", "101", "102", "103", "104", "105"]  # six 5m bars a session


async def test_the_warmup_is_the_last_bars_that_had_closed_by_now_and_no_more() -> None:
    reader = InMemoryCandles(trading_days(3, CLOSES))  # 18 bars over three sessions
    now = T0 + timedelta(days=2, minutes=12)  # into the third session: two of its bars have closed
    warmup = RepositoryWarmup(reader, FixedClock(now), bars=8)

    bars = await warmup.bars_for(make_config())

    assert len(bars) == 8
    assert all(bar.closes_at <= now for bar in bars)  # nothing from the bar still forming
    assert [bar.ts for bar in bars] == sorted(bar.ts for bar in bars)
    assert bars[-1].ts == T0 + timedelta(days=2, minutes=5)  # the newest closed bar


async def test_a_strategy_over_several_instruments_is_warmed_for_each() -> None:
    other = "NSE:1002"
    reader = InMemoryCandles(
        [*trading_days(2, CLOSES), *trading_days(2, CLOSES, instrument_id=other)]
    )
    warmup = RepositoryWarmup(reader, FixedClock(T0 + timedelta(days=2)), bars=4)

    bars = await warmup.bars_for(make_config(instruments=(INSTRUMENT, other)))

    assert {bar.instrument_id for bar in bars} == {INSTRUMENT, other}
    assert len(bars) == 8  # the depth is per instrument


async def test_with_no_history_the_warmup_is_empty_not_an_error() -> None:
    warmup = RepositoryWarmup(InMemoryCandles([]), FixedClock(T0))

    assert not await warmup.bars_for(make_config())

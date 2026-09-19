"""EM-52: 5m/15m/1h bars derive from closed 1m bars only, and are consistent by construction."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.marketdata.timeframes import BucketRule, TimeframeDeriver, derive, fold_bucket
from tests.support.fakes import CandleCollector

FRIDAY = date(2026, 9, 18)


def ist(hour: int, minute: int, day: date = FRIDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).astimezone(UTC)


def minute_bar(
    at: datetime,
    o: str = "100",
    h: str | None = None,
    low: str | None = None,
    c: str | None = None,
    volume: int = 10,
    partial: bool = False,
    instrument_id: str = "NSE:1",
) -> Candle:
    high = h or max(o, c or o, key=float)
    lo = low or min(o, c or o, key=float)
    return Candle(
        instrument_id, Timeframe.M1, at, Money.of(o), Money.of(high), Money.of(lo),
        Money.of(c or o), volume, partial,
    )  # fmt: skip


def session_series(instrument_id: str = "NSE:1", seed: int = 0) -> list[Candle]:
    """A full 375-minute session of random-but-valid 1m bars."""
    rng = random.Random(seed)
    price = 100.0
    bars = []
    for i in range(375):
        o = price
        c = round(o + rng.uniform(-0.5, 0.5), 2)
        hi = round(max(o, c) + rng.uniform(0, 0.3), 2)
        lo = round(min(o, c) - rng.uniform(0, 0.3), 2)
        at = ist(9, 15) + timedelta(minutes=i)
        bars.append(
            minute_bar(
                at,
                f"{o:.2f}",
                f"{hi:.2f}",
                f"{lo:.2f}",
                f"{c:.2f}",
                rng.randint(0, 900),
                instrument_id=instrument_id,
            )
        )
        price = c
    return bars


def stream(bars: list[Candle]) -> CandleCollector:
    deriver, out = TimeframeDeriver(), CandleCollector()
    deriver.subscribe(out)
    for bar in bars:
        deriver.on_candle(bar)
    return out


def test_buckets_align_to_the_session_open() -> None:
    rule = BucketRule()
    assert rule.start(ist(9, 15), Timeframe.M5) == ist(9, 15)
    assert rule.start(ist(9, 19), Timeframe.M5) == ist(9, 15)
    assert rule.start(ist(9, 20), Timeframe.M5) == ist(9, 20)
    assert rule.start(ist(9, 44), Timeframe.M15) == ist(9, 30)
    assert rule.start(ist(10, 14), Timeframe.H1) == ist(9, 15)
    assert rule.start(ist(10, 15), Timeframe.H1) == ist(10, 15)


def test_the_last_hourly_bucket_is_the_15_minutes_left_in_the_session() -> None:
    rule = BucketRule()
    assert rule.end(ist(15, 15), Timeframe.H1) == ist(15, 30)
    assert rule.expected_minutes(ist(15, 15), Timeframe.H1) == 15
    assert rule.expected_minutes(ist(9, 15), Timeframe.H1) == 60
    assert rule.expected_minutes(ist(9, 15), Timeframe.M5) == 5


def test_a_five_minute_bar_folds_ohlcv_and_emits_when_its_last_minute_closes() -> None:
    bars = [
        minute_bar(ist(9, 15), "100", "101", "99.5", "100.5", 10),
        minute_bar(ist(9, 16), "100.5", "103", "100", "102", 20),
        minute_bar(ist(9, 17), "102", "102.5", "98", "99", 30),
        minute_bar(ist(9, 18), "99", "100", "98.5", "99.5", 40),
    ]
    deriver, out = TimeframeDeriver(), CandleCollector()
    deriver.subscribe(out)
    for bar in bars:
        deriver.on_candle(bar)
    assert out.of(Timeframe.M5) == []  # 09:19 has not closed yet

    deriver.on_candle(minute_bar(ist(9, 19), "99.5", "101", "99", "100.8", 50))

    (m5,) = out.of(Timeframe.M5)
    assert m5.ts == ist(9, 15) and m5.timeframe is Timeframe.M5
    assert (m5.open, m5.high, m5.low, m5.close) == (
        Money.of("100"),
        Money.of("103"),
        Money.of("98"),
        Money.of("100.8"),
    )
    assert m5.volume == 150 and m5.partial is False


def test_a_full_session_yields_75_five_minute_25_fifteen_minute_and_7_hourly_bars() -> None:
    out = stream(session_series())
    assert (len(out.of(Timeframe.M5)), len(out.of(Timeframe.M15)), len(out.of(Timeframe.H1))) == (
        75,
        25,
        7,
    )
    assert not any(c.partial for c in out.candles)


def test_streaming_derivation_equals_batch_derivation_for_any_series() -> None:
    """Consistency by construction: the two paths cannot disagree."""
    for seed in range(5):
        bars = session_series(seed=seed)
        streamed = sorted(stream(bars).candles, key=lambda c: (c.timeframe.value, c.ts))
        batch = sorted(derive(bars), key=lambda c: (c.timeframe.value, c.ts))
        assert streamed == batch


def test_higher_timeframes_are_consistent_with_each_other() -> None:
    bars = session_series(seed=3)
    out = stream(bars)
    hourly = {c.ts: c for c in out.of(Timeframe.H1)}
    for quarter in out.of(Timeframe.M15):
        hour = hourly[BucketRule().start(quarter.ts, Timeframe.H1)]
        assert hour.low <= quarter.low and quarter.high <= hour.high
    assert sum(c.volume for c in out.of(Timeframe.H1)) == sum(b.volume for b in bars)
    assert sum(c.volume for c in out.of(Timeframe.M5)) == sum(b.volume for b in bars)


def test_derivation_is_order_independent_and_idempotent() -> None:
    bars = session_series(seed=7)
    shuffled = bars[:]
    random.Random(1).shuffle(shuffled)
    assert derive(shuffled) == derive(bars) == derive(bars + bars)  # repeats change nothing


def test_a_missing_minute_makes_the_bucket_partial_but_it_still_emits_on_its_last_minute() -> None:
    bars = [minute_bar(ist(9, 15 + m)) for m in range(5) if m != 2]  # 09:17 is missing

    (m5,) = stream(bars).of(Timeframe.M5)

    assert m5.ts == ist(9, 15) and m5.partial is True
    assert m5.volume == 40  # four minutes of 10


def test_if_the_last_minute_is_the_missing_one_the_bucket_emits_when_the_next_begins() -> None:
    bars = [minute_bar(ist(9, 15 + m)) for m in range(4)]  # 09:19 never arrives
    deriver, out = TimeframeDeriver(), CandleCollector()
    deriver.subscribe(out)
    for bar in bars:
        deriver.on_candle(bar)
    assert out.of(Timeframe.M5) == []

    deriver.on_candle(minute_bar(ist(9, 20)))

    (m5,) = out.of(Timeframe.M5)
    assert m5.ts == ist(9, 15) and m5.partial is True


def test_a_partial_constituent_makes_the_derived_bar_partial() -> None:
    bars = [minute_bar(ist(9, 15 + m), partial=(m == 3)) for m in range(5)]
    (m5,) = stream(bars).of(Timeframe.M5)
    assert m5.partial is True


def test_a_repeated_minute_is_not_double_counted_in_the_stream() -> None:
    bars = [minute_bar(ist(9, 15 + m)) for m in range(5)]
    out = stream(bars[:2] + bars[:2] + bars[2:])  # 09:15 and 09:16 arrive twice
    (m5,) = out.of(Timeframe.M5)
    assert m5.volume == 50


def test_a_minute_arriving_after_its_bucket_was_emitted_is_ignored_and_counted() -> None:
    deriver, out = TimeframeDeriver(), CandleCollector()
    deriver.subscribe(out)
    for m in range(5):
        deriver.on_candle(minute_bar(ist(9, 15 + m)))
    deriver.on_candle(minute_bar(ist(9, 20)))  # bucket 09:20 opens

    deriver.on_candle(minute_bar(ist(9, 16)))  # stale: 09:15's bucket is long gone

    assert deriver.stale_minutes_ignored >= 1
    assert len([c for c in out.of(Timeframe.M5) if c.ts == ist(9, 15)]) == 1  # never re-emitted


def test_instruments_are_derived_independently() -> None:
    a = [minute_bar(ist(9, 15 + m), "100", instrument_id="NSE:1") for m in range(5)]
    b = [minute_bar(ist(9, 15 + m), "200", instrument_id="NSE:2") for m in range(5)]
    interleaved = [bar for pair in zip(a, b, strict=True) for bar in pair]
    out = stream(interleaved)
    assert {c.instrument_id: str(c.close.amount) for c in out.of(Timeframe.M5)} == {
        "NSE:1": "100",
        "NSE:2": "200",
    }


def test_only_one_minute_bars_can_be_derived_from() -> None:
    m5 = fold_bucket([minute_bar(ist(9, 15))], Timeframe.M5, ist(9, 15), 5)
    assert stream([m5]).candles == []  # the streaming deriver ignores non-1m input
    with pytest.raises(ValueError, match="1m"):
        derive([m5])

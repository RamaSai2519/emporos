"""EM-186: our candles built from the recorded live feed vs the broker's own candles.

The recording (15:22-15:25 IST) is replayed through the real parser, normalizer and aggregator and
compared, per instrument, with `getCandleData` for the same minutes. **They do not agree**, and the
strict xfail below is how that finding is kept machine-checkable rather than buried: the recorded
QUOTE frames carried a frozen last price and cumulative volume for those minutes, while the broker's
1m bars kept moving (in the second sample, 15:28, the feed volume caught up by exactly the broker's
15:28 bar volume). Whether that is a late-session (15:20-15:30) feed behaviour or a general one is
UNRESOLVED and needs a mid-session recording; until then the comparison criterion is not a PASS.
If the feed or the pipeline changes so the comparison holds, the strict xfail turns into a failure
that forces this note (and docs/live-trading.md) to be updated.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.marketdata.comparison import CandleComparator
from tests.support.recorded_feed import RecordedFrames, RecordedSessionReplay, broker_history

pytestmark = pytest.mark.contract


def _complete_minutes(candles: list[Candle]) -> list[Candle]:
    return [c for c in candles if not c.partial]


@pytest.mark.xfail(
    strict=True,
    reason="EM-186: recorded 15:22-15:25 IST feed frames were frozen while broker bars moved",
)
def test_replayed_live_candles_match_the_brokers_within_tolerance() -> None:
    ours = _complete_minutes(RecordedSessionReplay(RecordedFrames()).candles())
    theirs = broker_history()
    comparator = CandleComparator()
    for instrument in sorted({c.instrument_id for c in ours}):
        mine = [c for c in ours if c.instrument_id == instrument]
        first, last = min(c.ts for c in mine), max(c.ts for c in mine)
        window = [b for b in theirs if b.instrument_id == instrument and first <= b.ts <= last]
        report = comparator.compare(mine, window)
        assert report.compared > 0
        assert report.agrees, f"{instrument}: {len(report.mismatches)} mismatches"


def test_the_comparison_finds_real_differences_rather_than_passing_vacuously() -> None:
    ours = _complete_minutes(RecordedSessionReplay(RecordedFrames()).candles())
    mine = [c for c in ours if c.instrument_id == "NSE:3045"]
    window = [
        b
        for b in broker_history()
        if b.instrument_id == "NSE:3045" and mine[0].ts <= b.ts <= mine[-1].ts
    ]

    report = CandleComparator().compare(mine, window)

    assert report.compared >= 3 and report.mismatches


def test_the_brokers_5m_bars_are_exactly_the_aggregate_of_its_1m_bars() -> None:
    """Broker-side consistency: what a 5m rollup of 1m history must reproduce (3 of the 4
    instruments agree on every bar; INFY has one bar that does not, kept as a known exception)."""
    ones = {(b.instrument_id, b.ts): b for b in broker_history(Timeframe.M1)}
    checked = disagreeing = 0
    for five in broker_history(Timeframe.M5):
        parts = [ones.get((five.instrument_id, five.ts + timedelta(minutes=i))) for i in range(5)]
        bars = [p for p in parts if p is not None]
        if not bars:
            continue
        checked += 1
        same = (
            sum(p.volume for p in bars) == five.volume
            and bars[0].open == five.open
            and bars[-1].close == five.close
            and max(p.high.amount for p in bars) == five.high.amount
            and min(p.low.amount for p in bars) == five.low.amount
        )
        disagreeing += not same
    assert checked == 4 * 74
    assert disagreeing <= 1


def test_the_recorded_bars_carry_utc_timestamps_starting_at_the_0915_ist_open() -> None:
    first = min(b.ts for b in broker_history())
    assert first == datetime.fromisoformat("2026-09-24T03:45:00+00:00")

from datetime import timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.candles import Timeframe
from emporos.strategies.history import ClosedBarHistory
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, bar_at


def _history(minutes_in: int = 0) -> tuple[FixedClock, ClosedBarHistory]:
    clock = FixedClock(T0 + timedelta(minutes=minutes_in))
    return clock, ClosedBarHistory(clock)


def test_only_bars_closed_at_the_clock_time_are_readable() -> None:
    clock, history = _history()
    for minute in (0, 5, 10):
        history.record(bar_at(minutes=minute))

    assert history.bars(INSTRUMENT, Timeframe.M5, 10) == ()  # the 09:15 bar closes at 09:20

    clock.set(T0 + timedelta(minutes=10))
    assert [b.ts for b in history.bars(INSTRUMENT, Timeframe.M5, 10)] == [
        T0,
        T0 + timedelta(minutes=5),
    ]  # the 09:25 bar is held but cannot be read: it has not closed


def test_a_future_bar_becomes_readable_only_when_the_clock_reaches_its_close() -> None:
    clock, history = _history()
    history.record(bar_at(minutes=5))

    clock.set(T0 + timedelta(minutes=9, seconds=59))
    assert history.bars(INSTRUMENT, Timeframe.M5, 1) == ()
    clock.set(T0 + timedelta(minutes=10))
    assert len(history.bars(INSTRUMENT, Timeframe.M5, 1)) == 1


def test_limit_returns_the_most_recent_bars_oldest_first() -> None:
    _, history = _history(minutes_in=60)
    for minute in range(0, 50, 5):
        history.record(bar_at(minutes=minute, close=str(100 + minute)))

    latest = history.bars(INSTRUMENT, Timeframe.M5, 3)

    assert [b.ts for b in latest] == [T0 + timedelta(minutes=m) for m in (35, 40, 45)]


def test_bars_recorded_out_of_order_are_kept_in_time_order() -> None:
    _, history = _history(minutes_in=60)
    for minute in (10, 0, 5):
        history.record(bar_at(minutes=minute))

    assert [b.ts for b in history.bars(INSTRUMENT, Timeframe.M5, 10)] == [
        T0 + timedelta(minutes=m) for m in (0, 5, 10)
    ]


def test_recording_the_same_bar_again_replaces_it() -> None:
    _, history = _history(minutes_in=60)
    history.record(bar_at(close="100"))
    history.record(bar_at(close="101"))

    (only,) = history.bars(INSTRUMENT, Timeframe.M5, 10)
    assert str(only.close.amount) == "101"


def test_series_are_kept_apart_by_instrument_and_timeframe() -> None:
    _, history = _history(minutes_in=60)
    history.record(bar_at(INSTRUMENT))
    history.record(bar_at(OTHER_INSTRUMENT))
    history.record(bar_at(INSTRUMENT, timeframe=Timeframe.M15))

    assert len(history.bars(INSTRUMENT, Timeframe.M5, 10)) == 1
    assert len(history.bars(OTHER_INSTRUMENT, Timeframe.M5, 10)) == 1
    assert len(history.bars(INSTRUMENT, Timeframe.M15, 10)) == 1
    assert history.bars("NSE:9999", Timeframe.M5, 10) == ()


def test_the_store_is_bounded_and_forgets_the_oldest_bars() -> None:
    clock = FixedClock(T0 + timedelta(hours=5))
    history = ClosedBarHistory(clock, max_bars=3)
    for minute in range(0, 25, 5):
        history.record(bar_at(minutes=minute))

    assert [b.ts for b in history.bars(INSTRUMENT, Timeframe.M5, 10)] == [
        T0 + timedelta(minutes=m) for m in (10, 15, 20)
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_returns_nothing(limit: int) -> None:
    _, history = _history(minutes_in=60)
    history.record(bar_at())
    assert history.bars(INSTRUMENT, Timeframe.M5, limit) == ()


def test_the_bound_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_bars"):
        ClosedBarHistory(FixedClock(T0), max_bars=0)

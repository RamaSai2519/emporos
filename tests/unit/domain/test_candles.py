from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money

TS = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _candle(**overrides: object) -> Candle:
    base = Candle(
        instrument_id="inst-1",
        timeframe=Timeframe.M1,
        ts=TS,
        open=Money.of("100"),
        high=Money.of("102"),
        low=Money.of("99"),
        close=Money.of("101"),
        volume=500,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_a_valid_candle_is_constructible_and_not_partial_by_default() -> None:
    assert _candle().partial is False


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="UTC"):
        _candle(ts=datetime(2026, 1, 5, 4, 0))


def test_non_utc_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="UTC"):
        _candle(ts=datetime(2026, 1, 5, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))))


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(ValueError, match="volume"):
        _candle(volume=-1)


def test_low_above_high_is_rejected() -> None:
    with pytest.raises(ValueError, match="low"):
        _candle(low=Money.of("103"))


@pytest.mark.parametrize("field", ["open", "close"])
def test_open_and_close_must_lie_within_the_range(field: str) -> None:
    with pytest.raises(ValueError, match="within"):
        _candle(**{field: Money.of("150")})

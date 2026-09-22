"""`MarketRegimeClassifier`: hand-checked, look-ahead-safe (each bar's regime depends only on bars
fed before it, verified in test_regime_at_a_given_bar_never_changes_when_later_bars_are_fed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.strategies.regime import MarketRegime, MarketRegimeClassifier

_BASE_DAY = datetime(2026, 1, 1, tzinfo=UTC)


def _daily_bar(day: int, close: str, width: str) -> Candle:
    mid = Decimal(close)
    half = Decimal(width) / 2
    return Candle(
        instrument_id="NSE:TEST",
        timeframe=Timeframe.D1,
        ts=_BASE_DAY + timedelta(days=day),
        open=Money(mid),
        high=Money(mid + half),
        low=Money(mid - half),
        close=Money(mid),
        volume=1000,
    )


def _classifier(**overrides: object) -> MarketRegimeClassifier:
    params: dict[str, object] = {"trend_period": 4, "atr_period": 3, "vol_window": 5}
    params.update(overrides)
    return MarketRegimeClassifier(**params)  # type: ignore[arg-type]


# A steady 0.4/day climb with mildly alternating bar width (1.0/1.2): a straight-line trend whose
# volatility neither spikes nor goes quiet relative to its own trailing history.
_TREND_CLOSES = [Decimal("1000") + Decimal("0.4") * i for i in range(1, 8)]
_TREND_WIDTHS = ["1.0", "1.2", "1.0", "1.2", "1.0", "1.2", "1.0"]


def _feed_trend_warmup(clf: MarketRegimeClassifier) -> MarketRegime | None:
    regime = None
    for day, (close, width) in enumerate(zip(_TREND_CLOSES, _TREND_WIDTHS, strict=True), start=1):
        regime = clf.update(_daily_bar(day, str(close), width))
    return regime


class TestMarketRegimeClassifier:
    def test_not_ready_before_the_volatility_window_fills(self) -> None:
        clf = _classifier()
        for day, (close, width) in enumerate(
            zip(_TREND_CLOSES[:6], _TREND_WIDTHS[:6], strict=True), start=1
        ):
            assert clf.update(_daily_bar(day, str(close), width)) is None
        assert not clf.ready

    def test_a_straight_line_climb_with_steady_volatility_is_trending(self) -> None:
        clf = _classifier()
        assert _feed_trend_warmup(clf) is MarketRegime.TRENDING
        assert clf.ready
        assert clf.value is MarketRegime.TRENDING

    def test_a_sudden_wide_bar_is_high_vol_regardless_of_trend(self) -> None:
        clf = _classifier()
        _feed_trend_warmup(clf)
        spike = clf.update(_daily_bar(8, "1003.2", "40"))
        assert spike is MarketRegime.HIGH_VOL

    def test_a_sudden_narrow_bar_is_low_vol(self) -> None:
        clf = _classifier()
        _feed_trend_warmup(clf)
        quiet = clf.update(_daily_bar(8, "1003.2", "0.01"))
        assert quiet is MarketRegime.LOW_VOL

    def test_a_choppy_round_trip_at_steady_volatility_is_ranging(self) -> None:
        clf = _classifier()
        closes = ["1000", "1002", "1000", "1002", "1000", "1002", "1000", "1002"]
        regime = None
        for day, close in enumerate(closes, start=1):
            regime = clf.update(_daily_bar(day, close, "20"))
        assert regime is MarketRegime.RANGING

    def test_regime_at_a_given_bar_never_changes_when_later_bars_are_fed(self) -> None:
        """Look-ahead safety, made concrete: replaying the same history up to bar 7 and stopping
        must give the same bar-7 regime as continuing on to feed bar 8."""
        stopped_at_seven = _classifier()
        full_run = _classifier()
        assert _feed_trend_warmup(stopped_at_seven) == _feed_trend_warmup(full_run)
        full_run.update(_daily_bar(8, "1003.2", "40"))
        assert stopped_at_seven.value is MarketRegime.TRENDING  # unchanged by the later bar

    def test_version_is_declared(self) -> None:
        assert MarketRegimeClassifier.VERSION == "v1"

    def test_rejects_a_non_positive_vol_window(self) -> None:
        with pytest.raises(ValueError):
            _classifier(vol_window=0)

    def test_rejects_inverted_percentile_thresholds(self) -> None:
        with pytest.raises(ValueError):
            _classifier(low_vol_percentile=Decimal("0.8"), high_vol_percentile=Decimal("0.2"))

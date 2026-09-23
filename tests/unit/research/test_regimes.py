"""EM-178: the segmentation axes (`VolatilityBucket`, `LiquidityBucket`, `MarketRegimeAxis`,
`SectorAxis`)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar

from emporos.research.regimes import (
    HIGH,
    LOW,
    LiquidityBucket,
    MarketRegimeAxis,
    MomentumTailAxis,
    SectorAxis,
    SpreadProxyBucket,
    VolatilityBucket,
)


def test_liquidity_bucket_needs_a_full_window_before_it_labels_anything() -> None:
    bucket = LiquidityBucket(window=3)

    labels = [bucket.update(bar(i, 10, volume=100)) for i in range(2)]

    assert labels == [None, None]


def test_liquidity_bucket_ranks_a_volume_spike_as_high() -> None:
    bucket = LiquidityBucket(window=3, low=Decimal("0.33"), high=Decimal("0.67"))

    for i, volume in enumerate([100, 100, 100]):
        bucket.update(bar(i, 10, volume=volume))
    label = bucket.update(bar(3, 10, volume=10_000))

    assert label == HIGH


def test_liquidity_bucket_ranks_a_volume_drought_as_low() -> None:
    bucket = LiquidityBucket(window=4, low=Decimal("0.33"), high=Decimal("0.67"))

    for i, volume in enumerate([100, 100, 100, 100]):
        bucket.update(bar(i, 10, volume=volume))
    label = bucket.update(bar(4, 10, volume=1))

    assert label == LOW


def test_volatility_bucket_needs_a_full_window_before_it_labels_anything() -> None:
    bucket = VolatilityBucket(window=3)

    labels = [bucket.update(bar(i, 10, high=11, low=9)) for i in range(2)]

    assert labels == [None, None]


def test_volatility_bucket_flags_a_wide_true_range_as_high() -> None:
    bucket = VolatilityBucket(window=3)
    for i in range(3):
        bucket.update(bar(i, 100, high=101, low=99))

    label = bucket.update(bar(3, 100, high=150, low=50))

    assert label == HIGH


def test_a_percentile_bucket_rejects_bad_thresholds() -> None:
    with pytest.raises(ValueError, match="0 <= low < high <= 1"):
        LiquidityBucket(low=Decimal("0.8"), high=Decimal("0.2"))


def test_spread_proxy_bucket_needs_a_full_window_before_it_labels_anything() -> None:
    bucket = SpreadProxyBucket(window=3)

    labels = [bucket.update(bar(i, 100, volume=100)) for i in range(2)]

    assert labels == [None, None]


def test_spread_proxy_bucket_flags_a_big_move_on_thin_volume_as_high() -> None:
    bucket = SpreadProxyBucket(window=3, low=Decimal("0.33"), high=Decimal("0.67"))
    for i, close in enumerate([100, 100, 100]):
        bucket.update(bar(i, close, volume=1000))

    label = bucket.update(bar(3, 200, volume=10))  # a +100% move on almost no volume

    assert label == HIGH


def test_spread_proxy_bucket_flags_a_tiny_move_on_heavy_volume_as_low() -> None:
    bucket = SpreadProxyBucket(window=4, low=Decimal("0.33"), high=Decimal("0.67"))
    for i, close in enumerate([100, 101, 100, 101]):
        bucket.update(bar(i, close, volume=1000))

    label = bucket.update(bar(4, Decimal("101.01"), volume=1_000_000))  # tiny move, huge volume

    assert label == LOW


def test_spread_proxy_bucket_skips_a_bar_with_no_trades() -> None:
    bucket = SpreadProxyBucket(window=2)

    assert bucket.update(bar(0, 100, volume=0)) is None


def test_momentum_tail_axis_needs_a_full_window_before_it_labels_anything() -> None:
    axis = MomentumTailAxis(k_bars=1, window=3)

    labels = [axis.update(bar(i, 100 + i)) for i in range(3)]

    assert labels == [None, None, None]


def test_momentum_tail_axis_flags_a_burst_of_gains_as_high() -> None:
    axis = MomentumTailAxis(k_bars=1, window=3)
    for i, price in enumerate([100, 101, 102, 103]):
        axis.update(bar(i, price))

    label = axis.update(bar(4, 200))

    assert label == HIGH


def test_market_regime_axis_wraps_the_live_classifier() -> None:
    axis = MarketRegimeAxis()

    # Not enough history yet for the live classifier to have an opinion.
    assert axis.update(bar(0, 100, high=101, low=99)) is None
    assert axis.name == "market_regime"


def test_sector_axis_delegates_to_the_injected_lookup() -> None:
    class FixedSector:
        def sector_of(self, instrument_id: str) -> str | None:
            return "IT" if instrument_id == "NSE:1" else None

    axis = SectorAxis(FixedSector())

    assert axis.update(bar(0, 100, instrument_id="NSE:1")) == "IT"
    assert axis.update(bar(0, 100, instrument_id="NSE:2")) is None

"""EM-181: order-flow/liquidity-proxy features computed from OHLCV alone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.unit.research.conftest import bar

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.research.features import CausalHistory
from emporos.research.order_flow import (
    OrderFlowImbalance,
    RelativeVolumeTimeOfDay,
    SpreadProxyFeature,
    TradeIntensity,
)
from emporos.research.regimes import amihud_illiquidity

D = Decimal


def _at(ts: datetime, close: Decimal | int, volume: int = 1000) -> Candle:
    amount = Money.of(Decimal(close))
    return Candle(
        instrument_id="NSE:1", timeframe=Timeframe.M5, ts=ts,
        open=amount, high=amount, low=amount, close=amount, volume=volume,
    )  # fmt: skip


def test_order_flow_imbalance_is_positive_when_the_bar_closes_at_its_high() -> None:
    candle = bar(0, 110, high=110, low=90, volume=1000)
    feature = OrderFlowImbalance()

    value = feature.compute(CausalHistory([candle], 0))

    assert value == D(1000)  # ((110-90)-(110-110))/(110-90) * 1000 = 1.0 * 1000


def test_order_flow_imbalance_is_negative_when_the_bar_closes_at_its_low() -> None:
    candle = bar(0, 90, high=110, low=90, volume=1000)
    feature = OrderFlowImbalance()

    value = feature.compute(CausalHistory([candle], 0))

    assert value == D(-1000)


def test_order_flow_imbalance_is_none_for_a_flat_bar() -> None:
    candle = bar(0, 100, high=100, low=100, volume=1000)
    feature = OrderFlowImbalance()

    assert feature.compute(CausalHistory([candle], 0)) is None


def test_relative_volume_time_of_day_needs_prior_sessions() -> None:
    day1_open = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    bars = [_at(day1_open, 100), _at(day1_open + timedelta(minutes=5), 101)]
    feature = RelativeVolumeTimeOfDay(window=5)

    assert feature.compute(CausalHistory(bars, 1)) is None


def test_relative_volume_time_of_day_compares_the_same_slot_across_days() -> None:
    day1_open = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    day2_open = datetime(2026, 3, 3, 3, 45, tzinfo=UTC)
    day3_open = datetime(2026, 3, 4, 3, 45, tzinfo=UTC)
    bars = [
        _at(day1_open, 100, volume=1000), _at(day1_open + timedelta(minutes=5), 101, volume=500),
        _at(day2_open, 100, volume=1000), _at(day2_open + timedelta(minutes=5), 101, volume=500),
        _at(day3_open, 100, volume=1000), _at(day3_open + timedelta(minutes=5), 101, volume=2000),
    ]  # fmt: skip
    feature = RelativeVolumeTimeOfDay(window=5)

    # index 5 = day3's second bar (same slot as the 500-volume bars on days 1 and 2)
    value = feature.compute(CausalHistory(bars, 5))

    assert value == D(3)  # (2000 - 500) / 500


def test_trade_intensity_needs_a_full_window() -> None:
    bars = [bar(i, 100, volume=1000) for i in range(3)]
    feature = TradeIntensity(window=5)

    assert feature.compute(CausalHistory(bars, 2)) is None


def test_trade_intensity_reads_a_volume_spike_as_positive() -> None:
    bars = [bar(i, 100, volume=1000) for i in range(5)] + [bar(5, 100, volume=3000)]
    feature = TradeIntensity(window=5)

    value = feature.compute(CausalHistory(bars, 5))

    assert value == D(2)  # (3000 - 1000) / 1000


def test_spread_proxy_feature_is_none_with_no_trades() -> None:
    candle = bar(0, 100, volume=0)
    feature = SpreadProxyFeature()

    assert feature.compute(CausalHistory([candle], 0)) is None


def test_spread_proxy_feature_is_stateless_across_reused_calls() -> None:
    """A regression guard for the exact bug documented in the class: reusing one instance across
    two different instruments' bar sequences must not leak the first instrument's last close into
    the second instrument's first-bar reading."""
    feature = SpreadProxyFeature()
    instrument_a = [bar(0, 100), bar(1, 200)]
    instrument_b = [bar(0, 50)]

    feature.compute(CausalHistory(instrument_a, 1))  # warm up as if A were evaluated first
    value = feature.compute(CausalHistory(instrument_b, 0))

    assert value == amihud_illiquidity(None, instrument_b[0].close.amount, instrument_b[0].volume)

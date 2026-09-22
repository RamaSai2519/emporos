"""regime_selector_v1 (EM-126) on hand-built days whose outcome can be checked by eye. The daily
bar sequences below are the exact ones hand-verified in test_regime.py to classify as TRENDING,
RANGING, HIGH_VOL and LOW_VOL under `MarketRegimeClassifier`'s default parameters (which this
strategy's own defaults match) -- reused here rather than re-derived, since the classifier's own
correctness is that file's job, not this one's."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from tests.unit.strategies.test_intraday_strategies import ALPHA, DAY1, Harness, bar

_DAILY_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def daily_bar(day: int, close: str, width: str) -> Candle:
    mid, half = Decimal(close), Decimal(width) / 2
    return Candle(
        instrument_id=ALPHA, timeframe=Timeframe.D1, ts=_DAILY_BASE + timedelta(days=day),
        open=Money(mid), high=Money(mid + half), low=Money(mid - half), close=Money(mid),
        volume=1000,
    )  # fmt: skip


# The 7-bar climb verified TRENDING at bar 7 in test_regime.py.
_TREND_CLOSES = [Decimal("1000") + Decimal("0.4") * i for i in range(1, 8)]
_TREND_WIDTHS = ["1.0", "1.2", "1.0", "1.2", "1.0", "1.2", "1.0"]
TRENDING_DAYS = [
    daily_bar(d, str(c), w)
    for d, (c, w) in enumerate(zip(_TREND_CLOSES, _TREND_WIDTHS, strict=True), start=1)
]

# The 8-bar choppy-at-steady-volatility sequence verified RANGING at bar 8 in test_regime.py.
RANGING_DAYS = [
    daily_bar(d, c, "20")
    for d, c in enumerate(["1000", "1002", "1000", "1002", "1000", "1002", "1000", "1002"], start=1)
]

# Continuing the trending climb with one wide-range day: verified HIGH_VOL in test_regime.py.
HIGH_VOL_DAYS = [*TRENDING_DAYS, daily_bar(8, "1003.2", "40")]

SELECTOR = {
    "regime_trend_period": 4, "regime_atr_period": 3, "regime_vol_window": 5,
    "regime_trend_threshold": "0.4", "breakout_lookback": 3, "atr_period": 3,
}  # fmt: skip


def _seeded(days: list[Candle], **overrides: object) -> Harness:
    h = Harness("regime_selector_v1", {**SELECTOR, **overrides})
    for d in days:
        h.ctx.history.record(d)  # pre-load; visible once the clock (advanced by feed()) passes it
    return h


def _flat_5m(day: datetime, start: int, count: int, price: str) -> list[Candle]:
    """Bars closing flat at `price` but with a small wick, so ATR warms up to a small positive
    number rather than exactly zero -- a zero ATR degenerates the VWAP-reversion threshold to
    "close <= vwap exactly", which a perfectly flat warm-up bar satisfies by construction and
    would trigger a phantom entry these bars are meant to avoid, not cause."""
    p = Decimal(price)
    high, low = str(p + Decimal("0.5")), str(p - Decimal("0.5"))
    return [bar(day, start + i, price, high, low, price) for i in range(count)]


class TestRegimeSelector:
    def test_no_trade_until_the_daily_history_gives_a_regime(self) -> None:
        h = Harness("regime_selector_v1", SELECTOR)  # no daily bars recorded at all

        breakout = bar(DAY1, 0, "1010", "1015", "1005", "1012")
        assert h.feed([breakout]) == []

    def test_trending_regime_trades_the_channel_breakout(self) -> None:
        h = _seeded(TRENDING_DAYS)
        # a flat opener rebuilds the 3-bar channel around 1002.8 without itself breaking out
        h.feed(_flat_5m(DAY1, 0, 3, "1002.8"))

        (signal,) = h.feed([bar(DAY1, 3, "1004", "1006", "1003.5", "1005.5")])

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "trending" in signal.reason

    def test_ranging_regime_trades_the_vwap_reversion(self) -> None:
        h = _seeded(RANGING_DAYS)
        # build a session VWAP near 1000 with a few flat bars, then extend far below it
        h.feed(_flat_5m(DAY1, 0, 3, "1000"))

        (signal,) = h.feed([bar(DAY1, 3, "985", "986", "950", "955")])  # far under VWAP

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "ranging" in signal.reason

    def test_high_vol_regime_sits_out_even_on_a_clean_breakout(self) -> None:
        h = _seeded(HIGH_VOL_DAYS)
        h.feed(_flat_5m(DAY1, 0, 3, "1002.8"))

        # the same breakout that was an entry in the trending test
        assert h.feed([bar(DAY1, 3, "1004", "1006", "1003.5", "1005.5")]) == []

    def test_ranging_regime_does_not_trade_a_channel_breakout(self) -> None:
        """The trend rule never fires outside a trending regime, even if price would have broken
        the channel -- the regime gate, not the sub-rule's own logic, decides which is live."""
        h = _seeded(RANGING_DAYS)
        h.feed(_flat_5m(DAY1, 0, 3, "1002.8"))

        assert h.feed([bar(DAY1, 3, "1004", "1006", "1003.5", "1005.5")]) == []

    def test_shorting_off_blocks_both_sub_rules(self) -> None:
        trend = _seeded(TRENDING_DAYS, allow_short=False)
        trend.feed(_flat_5m(DAY1, 0, 3, "1002.8"))
        assert trend.feed([bar(DAY1, 3, "1001", "1001.5", "999", "1000")]) == []  # would breakdown

        ranging = _seeded(RANGING_DAYS, allow_short=False)
        ranging.feed(_flat_5m(DAY1, 0, 3, "1000"))
        assert ranging.feed([bar(DAY1, 3, "1015", "1050", "1014", "1045")]) == []  # would extend up

    def test_at_most_max_entries_a_day(self) -> None:
        h = _seeded(TRENDING_DAYS, max_entries=1)
        h.feed(_flat_5m(DAY1, 0, 3, "1002.8"))
        (entry,) = h.feed([bar(DAY1, 3, "1004", "1006", "1003.5", "1005.5")])
        h.positions.set(ALPHA, entry.quantity, "1005.5")
        h.feed([bar(DAY1, 4, "990", "990", "980", "981")])  # stopped out hard
        h.positions.set(ALPHA, 0)

        assert h.feed([bar(DAY1, 5, "981", "1010", "980", "1009")]) == []  # no second entry today

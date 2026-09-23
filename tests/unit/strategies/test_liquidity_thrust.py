"""liquidity_thrust_v1 (EM-171/EM-173) on hand-built days whose outcome can be checked by eye. A
strategy is judged in backtests for profit; here it is judged for doing what it says."""

from __future__ import annotations

from datetime import timedelta

from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from tests.unit.strategies.test_intraday_strategies import ALPHA, DAY1, Harness, bar

# vol_window=5 warms the volume baseline up fast; atr_period=3 warms ATR even faster. min_range_bps
# is low enough that the hand-built bars below clear it without it being the thing under test.
THRUST = {
    "atr_period": 3, "vol_window": 5, "vol_surge_mult": "3.0", "close_location_min": "0.75",
    "min_range_bps": 10, "stop_atr_mult": "1.0", "target_atr_mult": "2.0",
}  # fmt: skip


def baseline_bars(count: int = 5, start: int = 0, price: str = "100", day=DAY1) -> list:
    """`count` narrow, unremarkable bars at the default test volume (1000): the rolling average
    the surge test is measured against."""
    return [bar(day, start + i, price, "100.3", "99.8", "100.1", volume=1000) for i in range(count)]


class TestLiquidityThrust:
    def test_nothing_trades_before_the_volume_window_fills(self) -> None:
        h = Harness("liquidity_thrust_v1", THRUST)

        # 4 bars: the average is not ready yet (needs 5), so even a huge-volume bar is ignored
        assert h.feed([*baseline_bars(4), bar(DAY1, 4, "103", "104", "102.5", "103.9", 5000)]) == []

    def test_high_volume_with_a_strong_close_near_the_high_is_a_long_entry(self) -> None:
        h = Harness("liquidity_thrust_v1", THRUST)
        h.feed(baseline_bars(5))  # average volume 1000 over the trailing window

        thrust = bar(DAY1, 5, "103", "104", "102.5", "103.9", volume=3500)  # 3.5x, close at 93%
        (signal,) = h.feed([thrust])

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "3500" in signal.reason and "1000" in signal.reason

    def test_high_volume_with_a_weak_close_in_the_middle_of_the_range_is_ignored(self) -> None:
        h = Harness("liquidity_thrust_v1", THRUST)
        h.feed(baseline_bars(5))

        # same 3.5x surge, but the close sits near the middle of the bar's range (47%)
        middling = bar(DAY1, 5, "103", "104", "102.5", "103.2", volume=3500)
        assert h.feed([middling]) == []

    def test_a_strong_close_without_a_volume_surge_is_ignored(self) -> None:
        h = Harness("liquidity_thrust_v1", THRUST)
        h.feed(baseline_bars(5))

        # close at 93% of the range, but volume is exactly the trailing average -- no surge
        no_surge = bar(DAY1, 5, "103", "104", "102.5", "103.9", volume=1000)
        assert h.feed([no_surge]) == []

    def test_high_volume_with_a_close_near_the_low_is_a_short_unless_shorting_is_off(self) -> None:
        on = Harness("liquidity_thrust_v1", THRUST)
        off = Harness("liquidity_thrust_v1", {**THRUST, "allow_short": False})
        on.feed(baseline_bars(5))
        off.feed(baseline_bars(5))

        thrust_down = bar(DAY1, 5, "103.4", "104", "102.5", "102.65", volume=3500)  # close at 10%
        (short,) = on.feed([thrust_down])
        assert short.side is OrderSide.SELL and short.kind is SignalKind.ENTRY
        assert off.feed([thrust_down]) == []

    def test_a_range_too_narrow_to_clear_min_range_bps_is_ignored(self) -> None:
        # min_range_bps raised well above what this bar's tiny range can clear, even with a
        # qualifying surge and an extreme close
        h = Harness("liquidity_thrust_v1", {**THRUST, "min_range_bps": 500})
        h.feed(baseline_bars(5))

        tiny_range = bar(DAY1, 5, "100", "100.05", "99.98", "100.04", volume=3500)
        assert h.feed([tiny_range]) == []

    def test_stop_and_target_are_atr_multiples_from_entry(self) -> None:
        h = Harness("liquidity_thrust_v1", THRUST)
        h.feed(baseline_bars(5))
        (entry,) = h.feed([bar(DAY1, 5, "103", "104", "102.5", "103.9", volume=3500)])
        h.positions.set(ALPHA, entry.quantity, "103.9")

        # ATR after this bar (period 3, seeded from the baseline's ~0.5 true ranges and this bar's
        # wide 1.5 range) sits well clear of a normal follow-through bar; only a genuine breach
        # of the wide stop or target should exit.
        assert h.feed([bar(DAY1, 6, "104", "105", "103.5", "104.5")]) == []  # inside both
        (exit_,) = h.feed([bar(DAY1, 7, "108", "112", "108", "111")])  # well through the target
        assert (exit_.kind, exit_.side) == (SignalKind.EXIT, OrderSide.SELL)
        assert "target hit" in exit_.reason

    def test_at_most_max_entries_a_day(self) -> None:
        h = Harness("liquidity_thrust_v1", {**THRUST, "max_entries": 1})
        h.feed(baseline_bars(5))
        (entry,) = h.feed([bar(DAY1, 5, "103", "104", "102.5", "103.9", volume=3500)])
        h.positions.set(ALPHA, entry.quantity, "103.9")
        h.feed([bar(DAY1, 6, "95", "95", "80", "82")])  # stopped out hard
        h.positions.set(ALPHA, 0)

        # another qualifying thrust the same day: no second entry
        again = bar(DAY1, 7, "83", "90", "82", "89.5", volume=4000)
        assert h.feed([again]) == []

    def test_the_volume_baseline_is_not_reset_at_the_session_boundary(self) -> None:
        """Like squeeze_breakout_v1's volatility read, the trailing volume average is about the
        instrument's own recent bars, not "today so far": a strategy that saw day 1's baseline
        can act on a day-2 thrust bar immediately, while a fresh strategy given only that one bar
        has no average to compare it against yet."""
        day2 = DAY1 + timedelta(days=1)
        thrust = bar(day2, 0, "103", "104", "102.5", "103.9", volume=3500)

        carried = Harness("liquidity_thrust_v1", THRUST)
        carried.feed(baseline_bars(5))
        (signal,) = carried.feed([thrust])
        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)

        fresh = Harness("liquidity_thrust_v1", THRUST)
        assert fresh.feed([thrust]) == []  # no baseline yet at all

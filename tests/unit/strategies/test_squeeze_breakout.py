"""squeeze_breakout_v1 (EM-148) on hand-built days whose outcome can be checked by eye. A strategy
is judged in backtests for profit; here it is judged for doing what it says."""

from __future__ import annotations

from datetime import timedelta

from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from tests.unit.strategies.test_intraday_strategies import ALPHA, DAY1, Harness, bar

# Small enough to warm up in a handful of bars. squeeze_percentile "0.4" with a 5-bar window means
# a bar ranks in the bottom 2 of its trailing 5 to count as squeezed.
SQUEEZE = {
    "atr_period": 3, "squeeze_window": 5, "squeeze_percentile": "0.4", "min_squeeze_bars": 2,
    "breakout_lookback": 3, "breakout_buffer_bps": 5, "stop_atr_mult": "1.0",
    "target_atr_mult": "2.0",
}  # fmt: skip


def tight_bars(
    count: int, start: int = 0, first_close: float = 100.0, step: float = 0.2, day=DAY1
) -> list:
    """`count` narrow bars (high-low = 1.0), closes creeping up so ATR stays ~flat while ATR% (the
    percentile input) drifts down bar over bar -- each new bar ranks lowest in its own window."""
    out = []
    for i in range(count):
        close = first_close + step * i
        out.append(bar(day, start + i, f"{close}", f"{close + 0.5}", f"{close - 0.5}", f"{close}"))
    return out


class TestSqueezeBreakout:
    def test_nothing_trades_before_the_volatility_window_fills(self) -> None:
        h = Harness("squeeze_breakout_v1", SQUEEZE)

        assert h.feed(tight_bars(6)) == []  # ATR ready at bar 2, window needs 5 more -> bar 6

    def test_a_breakout_with_no_prior_squeeze_is_ignored(self) -> None:
        h = Harness("squeeze_breakout_v1", SQUEEZE)
        h.feed(tight_bars(7))  # warms up; by construction the 7th bar is itself squeezed once,
        # not the required 2 in a row yet (streak == 1 after this bar)

        breakout = bar(DAY1, 7, "103", "103.5", "102.5", "103")
        assert h.feed([breakout]) == []

    def test_a_breakout_after_two_squeezed_bars_in_a_row_is_an_entry(self) -> None:
        h = Harness("squeeze_breakout_v1", SQUEEZE)
        h.feed(tight_bars(8))  # streak reaches 2 by the 8th bar (see the streak test below)

        (signal,) = h.feed([bar(DAY1, 8, "103", "104", "102.5", "103.5")])

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "squeeze" in signal.reason and "broke the 3-bar range" in signal.reason

    def test_a_breakdown_after_the_squeeze_is_a_short_unless_shorting_is_off(self) -> None:
        on = Harness("squeeze_breakout_v1", SQUEEZE)
        off = Harness("squeeze_breakout_v1", {**SQUEEZE, "allow_short": False})
        on.feed(tight_bars(8))
        off.feed(tight_bars(8))
        breakdown = bar(DAY1, 8, "101", "101.5", "100", "100.3")

        (short,) = on.feed([breakdown])
        assert short.side is OrderSide.SELL and short.kind is SignalKind.ENTRY
        assert off.feed([breakdown]) == []

    def test_a_close_within_the_buffer_of_the_range_is_not_a_breakout(self) -> None:
        h = Harness("squeeze_breakout_v1", SQUEEZE)
        h.feed(tight_bars(8))

        # the 3-bar channel high is ~101.9 (see the entry test); this stays inside it
        assert h.feed([bar(DAY1, 8, "101.5", "101.8", "101.3", "101.6")]) == []

    def test_stop_and_target_are_atr_multiples_from_entry(self) -> None:
        h = Harness("squeeze_breakout_v1", SQUEEZE)
        h.feed(tight_bars(8))
        (entry,) = h.feed([bar(DAY1, 8, "103", "104", "102.5", "103.5")])
        h.positions.set(ALPHA, entry.quantity, "103.5")

        # ATR 1.5333 at entry (the entry bar's own wide range counts): stop 103.5 - 1.5333 =
        # 101.9667, target 103.5 + 3.0667 = 106.5667
        assert h.feed([bar(DAY1, 9, "104", "105", "103", "104")]) == []  # inside both
        (exit_,) = h.feed([bar(DAY1, 10, "106", "108", "106", "107")])  # through the target
        assert (exit_.kind, exit_.side) == (SignalKind.EXIT, OrderSide.SELL)
        assert "target hit" in exit_.reason

    def test_at_most_max_entries_a_day(self) -> None:
        h = Harness("squeeze_breakout_v1", {**SQUEEZE, "max_entries": 1})
        h.feed(tight_bars(8))
        (entry,) = h.feed([bar(DAY1, 8, "103", "104", "102.5", "103.5")])
        h.positions.set(ALPHA, entry.quantity, "103.5")
        h.feed([bar(DAY1, 9, "99", "99", "90", "91")])  # stopped out hard
        h.positions.set(ALPHA, 0)

        assert h.feed([bar(DAY1, 10, "91", "92", "89", "89.5")]) == []  # no second entry today

    def test_squeeze_history_is_not_reset_at_the_session_boundary(self) -> None:
        """The volatility read (ATR% ranked against its own trailing window) is about the
        instrument's own recent bars, not "today so far" -- only the breakout channel and entry
        count reset at the session boundary (`start_bar`). Proven by contrast: the same short day-2
        prefix (3 bars rebuilding the channel, then a breakout) is an entry when day 1's volatility
        history carried in, but a fresh strategy given only those same day-2 bars has not seen
        enough bars yet to even rank a squeeze and stays flat."""
        day2 = DAY1 + timedelta(days=1)
        day2_tail = tight_bars(3, first_close=101.6, day=day2)  # rebuilds the 3-bar channel
        breakout = bar(day2, 3, "104", "105", "103.5", "104.5")

        carried = Harness("squeeze_breakout_v1", SQUEEZE)
        carried.feed(tight_bars(8))  # day 1: streak reaches 2 (see the entry test above)
        (signal,) = carried.feed([*day2_tail, breakout])

        fresh = Harness("squeeze_breakout_v1", SQUEEZE)
        assert fresh.feed([*day2_tail, breakout]) == []  # its 5-bar volatility window isn't full

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)

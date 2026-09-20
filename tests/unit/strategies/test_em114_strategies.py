"""vwap_trend_v1, donchian_v1, ema_pullback_v1, gap_go_v1 and gap_fade_v1 on hand-built days whose
outcome can be checked by eye. A strategy is judged in backtests for profit; here it is judged for
doing what it says."""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from tests.unit.strategies.test_intraday_strategies import ALPHA, DAY1, Harness, bar, flat_bars

DAY2 = DAY1 + timedelta(days=1)


def ramp(day, start: int, n: int, first: float, step: float) -> list[Candle]:  # type: ignore[no-untyped-def]
    """`n` bars each closing `step` from the last, opening half a step before their close."""
    bars = []
    for i in range(n):
        close = first + step * i
        opened = close - step / 2
        high, low = max(opened, close) + 0.1, min(opened, close) - 0.1
        bars.append(bar(day, start + i, f"{opened}", f"{high}", f"{low}", f"{close}"))
    return bars


class TestGapDayState:
    """Both gap strategies read the same gap and opening range from the bars they were shown."""

    GAP_UP: ClassVar[list[Candle]] = [
        bar(DAY2, 0, "102", "103", "101.5", "102"), bar(DAY2, 1, "102", "103", "101.5", "102"),
        bar(DAY2, 2, "102", "103", "101.5", "102"),
    ]  # fmt: skip

    def test_no_previous_close_means_no_gap_and_no_trade(self) -> None:
        h = Harness("gap_go_v1", {})

        assert h.feed([*self.GAP_UP, bar(DAY2, 3, "103", "104", "103", "103.5")]) == []


class TestGapGo:
    def harness(self, **overrides: object) -> Harness:
        h = Harness("gap_go_v1", {"range_bars": 3, **overrides})
        h.feed(flat_bars(DAY1, 0, 4, "100"))  # yesterday closes at 100
        return h

    def test_a_gap_up_that_breaks_its_opening_range_upward_is_a_long(self) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)  # +200 bps, range 101.5..103

        (signal,) = h.feed([bar(DAY2, 3, "103", "104", "103", "103.5")])

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "gap 200 bps" in signal.reason

    def test_a_gap_smaller_than_the_minimum_is_ignored(self) -> None:
        h = self.harness()
        small = [bar(DAY2, i, "100.5", "101", "100", "100.5") for i in range(3)]  # +50 bps
        h.feed(small)

        assert h.feed([bar(DAY2, 3, "101", "102", "101", "101.5")]) == []

    def test_a_gap_down_that_breaks_its_range_downward_is_a_short_unless_shorting_is_off(
        self,
    ) -> None:
        down = [bar(DAY2, i, "98", "98.5", "97", "98") for i in range(3)]  # -200 bps
        breakdown = bar(DAY2, 3, "97", "97", "96", "96.5")
        on = self.harness()
        on.feed(down)
        off = self.harness(allow_short=False)
        off.feed(down)

        (short,) = on.feed([breakdown])
        assert short.side is OrderSide.SELL and short.kind is SignalKind.ENTRY
        assert off.feed([breakdown]) == []

    def test_a_close_against_the_gap_direction_is_not_a_go(self) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)

        assert h.feed([bar(DAY2, 3, "102", "102", "100.5", "101")]) == []  # below the range

    def test_one_entry_a_day_then_the_stop_or_target_closes_it(self) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)
        (entry,) = h.feed([bar(DAY2, 3, "103", "104", "103", "103.5")])
        h.positions.set(ALPHA, entry.quantity, "103.5")

        assert h.feed([bar(DAY2, 4, "104", "105", "104", "104.5")]) == []  # inside stop and target
        (stop,) = h.feed([bar(DAY2, 5, "104", "104", "101", "101.2")])  # under the range low 101.5
        assert (stop.kind, stop.side) == (SignalKind.EXIT, OrderSide.SELL)
        assert "stopped" in stop.reason
        h.positions.set(ALPHA, 0)
        assert h.feed([bar(DAY2, 6, "103", "106", "103", "105")]) == []  # not a second entry

    def test_target_is_r_times_the_stop_distance(self) -> None:
        h = self.harness(target_r="2.0")
        h.feed(TestGapDayState.GAP_UP)
        (entry,) = h.feed([bar(DAY2, 3, "103", "104", "103", "103.5")])
        h.positions.set(ALPHA, entry.quantity, "103.5")

        # stop 101.5, distance 2.0, target 103.5 + 4.0 = 107.5
        assert h.feed([bar(DAY2, 4, "106", "107.4", "106", "107.4")]) == []
        (exit_,) = h.feed([bar(DAY2, 5, "107", "108", "107", "107.5")])
        assert "at target" in exit_.reason

    def test_nothing_after_the_entry_cut_off(self) -> None:
        h = Harness("gap_go_v1", {"range_bars": 3}, no_entries_after="09:30")
        h.feed(flat_bars(DAY1, 0, 4, "100"))
        h.feed(TestGapDayState.GAP_UP)

        assert h.feed([bar(DAY2, 3, "103", "104", "103", "103.5")]) == []  # closes 09:35


class TestGapFade:
    def harness(self, **overrides: object) -> Harness:
        h = Harness("gap_fade_v1", {"range_bars": 3, **overrides})
        h.feed(flat_bars(DAY1, 0, 4, "100"))
        return h

    def test_a_gap_up_that_fails_down_through_its_range_is_a_short_toward_the_old_close(
        self,
    ) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)

        (signal,) = h.feed([bar(DAY2, 3, "102", "102", "100.8", "101")])  # below 101.5

        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.SELL)
        assert "failed its opening range" in signal.reason

    def test_a_gap_that_holds_is_not_faded(self) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)

        assert h.feed([bar(DAY2, 3, "103", "104", "103", "103.5")]) == []

    def test_a_gap_down_that_recovers_is_a_long(self) -> None:
        h = self.harness()
        h.feed([bar(DAY2, i, "98", "98.5", "97", "98") for i in range(3)])  # range 97..98.5

        (signal,) = h.feed([bar(DAY2, 3, "98", "99.5", "98", "99")])

        assert signal.side is OrderSide.BUY

    def test_shorting_off_means_a_gap_up_is_never_faded(self) -> None:
        h = self.harness(allow_short=False)
        h.feed(TestGapDayState.GAP_UP)

        assert h.feed([bar(DAY2, 3, "102", "102", "100.8", "101")]) == []

    def test_the_short_covers_at_the_fraction_of_the_gap_asked_for(self) -> None:
        h = self.harness(fill_fraction="0.5")
        h.feed(TestGapDayState.GAP_UP)
        (entry,) = h.feed([bar(DAY2, 3, "102", "102", "100.8", "101")])
        h.positions.set(ALPHA, -entry.quantity, "101")

        # halfway from 101 to the old close 100 is 100.5
        assert h.feed([bar(DAY2, 4, "101", "101", "100.6", "100.6")]) == []
        (cover,) = h.feed([bar(DAY2, 5, "100.6", "100.6", "100.3", "100.4")])
        assert (cover.kind, cover.side) == (SignalKind.EXIT, OrderSide.BUY)
        assert "gap filled" in cover.reason

    def test_a_close_already_past_the_old_price_leaves_nothing_to_fade(self) -> None:
        h = self.harness()
        h.feed(TestGapDayState.GAP_UP)

        assert h.feed([bar(DAY2, 3, "100", "100", "99", "99.5")]) == []  # below the old close


DONCHIAN = {"lookback": 3, "atr_period": 3, "breakout_buffer_bps": 5}


class TestDonchian:
    def opening(self) -> list[Candle]:
        return [bar(DAY1, i, "100", "101", "99", "100") for i in range(3)]  # channel 99..101

    def test_nothing_trades_until_a_full_channel_exists(self) -> None:
        h = Harness("donchian_v1", DONCHIAN)

        early = [
            bar(DAY1, 0, "100", "101", "99", "100"),
            bar(DAY1, 1, "100", "104", "100", "103.9"),
            bar(DAY1, 2, "104", "106", "104", "105.9"),
        ]

        assert h.feed(early) == []

    def test_a_close_above_the_channel_is_a_long_and_below_it_a_short(self) -> None:
        up = Harness("donchian_v1", DONCHIAN)
        down = Harness("donchian_v1", DONCHIAN)

        (long_,) = up.feed([*self.opening(), bar(DAY1, 3, "101", "102.5", "101", "102")])
        (short,) = down.feed([*self.opening(), bar(DAY1, 3, "99", "99", "97.5", "98")])

        assert (long_.kind, long_.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert (short.kind, short.side) == (SignalKind.ENTRY, OrderSide.SELL)
        assert "3-bar channel" in long_.reason

    def test_a_close_inside_or_within_the_buffer_is_not_a_breakout(self) -> None:
        h = Harness("donchian_v1", DONCHIAN)

        assert h.feed([*self.opening(), bar(DAY1, 3, "100", "101.2", "100", "101.02")]) == []

    def test_the_channel_never_reaches_across_the_overnight_gap(self) -> None:
        h = Harness("donchian_v1", DONCHIAN)
        h.feed(self.opening())

        # a new day opening far above yesterday's channel: still no channel of its own
        assert h.feed([bar(DAY2, 0, "110", "111", "109", "110")]) == []
        assert h.feed([bar(DAY2, 1, "112", "113", "111", "113")]) == []

    def test_the_stop_trails_the_best_close_and_never_loosens(self) -> None:
        h = Harness("donchian_v1", {**DONCHIAN, "stop_atr_mult": "1.5", "trail_atr_mult": "2.0"})
        (entry,) = h.feed([*self.opening(), bar(DAY1, 3, "101", "102.5", "101", "102")])
        h.positions.set(ALPHA, entry.quantity, "102")

        assert h.feed([bar(DAY1, 4, "103", "106", "103", "105")]) == []  # a new best close
        assert (
            h.feed([bar(DAY1, 5, "104", "105", "104", "104.5")]) == []
        )  # a pullback, still inside
        (out,) = h.feed([bar(DAY1, 6, "103", "103", "98", "99")])  # far below the trailed stop
        assert (out.kind, out.side) == (SignalKind.EXIT, OrderSide.SELL)
        assert "trailed out" in out.reason

    def test_shorting_off_is_respected(self) -> None:
        h = Harness("donchian_v1", {**DONCHIAN, "allow_short": False})

        assert h.feed([*self.opening(), bar(DAY1, 3, "99", "99", "97.5", "98")]) == []

    def test_at_most_max_entries_a_day(self) -> None:
        h = Harness("donchian_v1", {**DONCHIAN, "max_entries": 1})
        (entry,) = h.feed([*self.opening(), bar(DAY1, 3, "101", "102.5", "101", "102")])
        h.positions.set(ALPHA, entry.quantity, "102")
        h.feed([bar(DAY1, 4, "103", "104", "94", "95")])  # stopped out
        h.positions.set(ALPHA, 0)

        assert h.feed([bar(DAY1, 5, "95", "95", "90", "91")]) == []


TREND = {"fast_ema": 2, "slow_ema": 4, "atr_period": 3, "skip_bars": 0, "stop_atr_mult": "1.5"}


class TestVwapTrend:
    def test_a_steady_rise_above_vwap_with_fast_over_slow_is_a_continuation_long(self) -> None:
        h = Harness("vwap_trend_v1", TREND)

        signals = h.feed(ramp(DAY1, 0, 8, 100, 1))

        assert signals and signals[0].side is OrderSide.BUY
        assert "continuation" in signals[0].reason

    def test_a_steady_fall_is_a_short_unless_shorting_is_off(self) -> None:
        on = Harness("vwap_trend_v1", TREND)
        off = Harness("vwap_trend_v1", {**TREND, "allow_short": False})

        assert on.feed(ramp(DAY1, 0, 8, 100, -1))[0].side is OrderSide.SELL
        assert off.feed(ramp(DAY1, 0, 8, 100, -1)) == []

    def test_a_flat_market_gives_no_trend_and_no_trade(self) -> None:
        assert Harness("vwap_trend_v1", TREND).feed(flat_bars(DAY1, 0, 12)) == []

    def test_pullback_mode_enters_on_the_bounce_and_says_so(self) -> None:
        h = Harness("vwap_trend_v1", {**TREND, "pullback": True})

        signals = h.feed(ramp(DAY1, 0, 8, 100, 1))

        assert signals and "pullback" in signals[0].reason

    def test_no_entries_in_the_first_skip_bars(self) -> None:
        h = Harness("vwap_trend_v1", {**TREND, "skip_bars": 20})

        assert h.feed(ramp(DAY1, 0, 8, 100, 1)) == []

    def test_losing_vwap_ends_the_trade_as_a_lost_trend(self) -> None:
        h = Harness("vwap_trend_v1", TREND)
        first = h.feed(ramp(DAY1, 0, 8, 100, 1))[0]
        h.positions.set(ALPHA, first.quantity, "104")

        (exit_,) = h.feed([bar(DAY1, 8, "106", "106", "101", "102")])  # under VWAP, inside the stop
        assert (exit_.kind, exit_.side) == (SignalKind.EXIT, OrderSide.SELL)

    def test_the_stop_sits_the_configured_atrs_away(self) -> None:
        h = Harness("vwap_trend_v1", {**TREND, "target_r": "1.0"})
        first = h.feed(ramp(DAY1, 0, 8, 100, 1))[0]
        h.positions.set(ALPHA, first.quantity, "104")

        # a huge close is at target (target_r 1 = the stop distance above the entry)
        (exit_,) = h.feed([bar(DAY1, 8, "106", "130", "106", "125")])
        assert "at target" in exit_.reason


PULLBACK = {"fast_ema": 3, "slow_ema": 6, "atr_period": 3, "skip_bars": 0}


def trend_then_dip() -> list[Candle]:
    """A firm rise, a dip to the fast EMA that keeps the slow EMA, then a bullish bounce."""
    rise = ramp(DAY1, 0, 10, 100, 1)  # closes 100..109
    dip = bar(DAY1, 10, "108.5", "108.6", "106", "108.4")  # low reaches the fast EMA, holds slow
    bounce = bar(DAY1, 11, "108.4", "110.2", "108.3", "110")  # bullish, back above the fast EMA
    return [*rise, dip, bounce]


class TestEmaPullback:
    def test_a_dip_to_the_fast_ema_that_holds_and_bounces_is_a_long(self) -> None:
        h = Harness("ema_pullback_v1", PULLBACK)

        signals = h.feed(trend_then_dip())

        assert signals, "expected the bounce to be bought"
        assert signals[-1].side is OrderSide.BUY and "pullback" in signals[-1].reason

    def test_a_straight_rise_with_no_dip_is_not_bought(self) -> None:
        h = Harness("ema_pullback_v1", {"skip_bars": 0})  # the real 9 and 20 EMAs

        assert h.feed(ramp(DAY1, 0, 40, 100, 1)) == []  # price stays well clear of the fast EMA

    def test_a_flat_market_is_not_a_trend(self) -> None:
        assert Harness("ema_pullback_v1", PULLBACK).feed(flat_bars(DAY1, 0, 14)) == []

    def test_the_mirror_in_a_down_trend_is_a_short_unless_shorting_is_off(self) -> None:
        fall = ramp(DAY1, 0, 10, 109, -1)
        rally = bar(DAY1, 10, "100.5", "103", "100.4", "100.6")  # high reaches the fast EMA
        bounce = bar(DAY1, 11, "100.6", "100.7", "98.8", "99")  # bearish, back under it
        on = Harness("ema_pullback_v1", PULLBACK)
        off = Harness("ema_pullback_v1", {**PULLBACK, "allow_short": False})

        assert on.feed([*fall, rally, bounce])[-1].side is OrderSide.SELL
        assert off.feed([*fall, rally, bounce]) == []

    def test_breaking_the_slow_ema_ends_the_trade(self) -> None:
        h = Harness("ema_pullback_v1", PULLBACK)
        entry = h.feed(trend_then_dip())[-1]
        h.positions.set(ALPHA, entry.quantity, "110")

        (exit_,) = h.feed([bar(DAY1, 12, "110", "110", "90", "95")])
        assert exit_.kind is SignalKind.EXIT and exit_.side is OrderSide.SELL

    def test_requiring_vwap_only_ever_removes_entries(self) -> None:
        loose = Harness("ema_pullback_v1", PULLBACK).feed(trend_then_dip())
        strict = Harness("ema_pullback_v1", {**PULLBACK, "use_vwap": True}).feed(trend_then_dip())

        assert len(strict) <= len(loose)

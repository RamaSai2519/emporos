"""orb_v1, vwap_reversion_v1 and rsi_pullback_v1 on hand-built days whose outcome can be checked by
eye. A strategy is judged in backtests for profit; here it is judged for doing what it says."""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.base import Strategy
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from tests.support.strategies import (
    INSTRUMENT,
    BookPositions,
    raw_config,
    resolved,
)

DAY1 = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)  # 09:15 IST, a Monday
ALPHA = "NSE:1001"


def bar(day: datetime, index: int, o: str, h: str, low: str, c: str, volume: int = 1000,
        partial: bool = False) -> Candle:  # fmt: skip
    return Candle(
        ALPHA, Timeframe.M5, day + timedelta(minutes=5 * index), Money.of(o), Money.of(h),
        Money.of(low), Money.of(c), volume, partial,
    )  # fmt: skip


def flat_bars(day: datetime, start: int, count: int, price: str = "100") -> list[Candle]:
    return [bar(day, start + i, price, price, price, price) for i in range(count)]


class Harness:
    """Feeds bars one at a time and collects the signals, with a positions book a test sets."""

    def __init__(
        self, name: str, parameters: dict[str, Any], no_entries_after: str = "15:00"
    ) -> None:
        raw = raw_config(name)
        raw["parameters"] = parameters
        raw["universe"] = {"type": "static", "instruments": ["NSE:ALPHA-EQ"]}
        raw["session"] = {"no_new_entries_after": no_entries_after, "square_off_at": "15:15"}
        self.config = resolved(raw)
        self.positions = BookPositions()
        self.clock = FixedClock(DAY1)
        from emporos.cli.strategy_composition import build_registry

        self.strategy: Strategy = build_registry().create(self.config)
        self.ctx = StrategyContext(
            run_id="run", config=self.config, clock=self.clock, logger=logging.getLogger("t"),
            history=ClosedBarHistory(self.clock), positions=self.positions, rng=random.Random(0),
        )  # fmt: skip
        self.strategy.initialize(self.ctx)
        self.signals: list[Signal] = []

    def feed(self, bars: Iterable[Candle]) -> list[Signal]:
        out: list[Signal] = []
        for b in bars:
            self.clock.set(b.closes_at)
            self.strategy.on_market_data(b)
            while (s := self.strategy.generate_signal()) is not None:
                out.append(s)
        self.signals += out
        return out


ORB = {
    "range_bars": 3, "breakout_buffer_bps": 5, "atr_period": 3, "stop_atr_mult": 1, "target_r": 2,
    "min_range_bps": 10, "max_range_bps": 500,
}  # fmt: skip


def orb_opening(day: datetime = DAY1) -> list[Candle]:
    """Three opening bars spanning 99..101, then two quiet bars to warm the ATR."""
    return [
        bar(day, 0, "100", "101", "99", "100"), bar(day, 1, "100", "101", "99", "100"),
        bar(day, 2, "100", "101", "99", "100"), bar(day, 3, "100", "100.5", "99.5", "100"),
    ]  # fmt: skip


class TestOrb:
    def test_a_close_above_the_opening_range_is_a_long_entry_sized_to_the_risk_budget(self) -> None:
        h = Harness("orb_v1", ORB)
        assert h.feed(orb_opening()) == []
        (signal,) = h.feed([bar(DAY1, 4, "100", "102.5", "100", "102")])
        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert signal.quantity == 490 and signal.limit_price == Money.of("102")  # 50,000 // 102
        assert "opening range 99..101" in signal.reason

    def test_a_close_below_the_range_is_a_short_entry_unless_shorting_is_off(self) -> None:
        breakdown = bar(DAY1, 4, "100", "100", "97.5", "98")
        (short,) = Harness("orb_v1", ORB).feed([*orb_opening(), breakdown])
        assert short.side is OrderSide.SELL and short.kind is SignalKind.ENTRY
        assert (
            Harness("orb_v1", {**ORB, "allow_short": False}).feed([*orb_opening(), breakdown]) == []
        )

    def test_nothing_happens_inside_the_range_or_without_a_close_beyond_it(self) -> None:
        h = Harness("orb_v1", ORB)
        wick = bar(DAY1, 4, "100", "103", "100", "100.9")  # pokes out but CLOSES inside
        assert h.feed([*orb_opening(), wick]) == []

    def test_a_range_that_is_too_narrow_or_too_wide_is_skipped(self) -> None:
        breakout = bar(DAY1, 4, "100", "102.5", "100", "102")
        assert (
            Harness("orb_v1", {**ORB, "min_range_bps": 300}).feed([*orb_opening(), breakout]) == []
        )
        assert (
            Harness("orb_v1", {**ORB, "max_range_bps": 50}).feed([*orb_opening(), breakout]) == []
        )

    def test_one_entry_per_day_then_the_stop_or_target_exits(self) -> None:
        h = Harness("orb_v1", ORB)
        (entry,) = h.feed([*orb_opening(), bar(DAY1, 4, "100", "102.5", "100", "102")])
        h.positions.set(ALPHA, entry.quantity, "102")
        assert (
            h.feed([bar(DAY1, 5, "102", "102.4", "101.8", "102.2")]) == []
        )  # neither stop nor target
        (exit_,) = h.feed([bar(DAY1, 6, "102", "102", "99", "99.5")])  # through the stop
        assert (exit_.kind, exit_.side, exit_.quantity) == (
            SignalKind.EXIT,
            OrderSide.SELL,
            entry.quantity,
        )
        assert "stopped" in exit_.reason
        h.positions.set(ALPHA, 0)
        assert (
            h.feed([bar(DAY1, 7, "100", "104", "100", "103")]) == []
        )  # a second breakout: not today

    def test_a_target_is_taken(self) -> None:
        h = Harness("orb_v1", ORB)
        (entry,) = h.feed([*orb_opening(), bar(DAY1, 4, "100", "102.5", "100", "102")])
        h.positions.set(ALPHA, entry.quantity, "102")
        (exit_,) = h.feed([bar(DAY1, 5, "102", "110", "102", "108")])
        assert "target" in exit_.reason

    def test_a_new_day_starts_a_new_range_and_may_trade_again(self) -> None:
        h = Harness("orb_v1", ORB)
        h.feed([*orb_opening(), bar(DAY1, 4, "100", "102.5", "100", "102")])
        day2 = DAY1 + timedelta(days=1)
        signals = h.feed([*orb_opening(day2), bar(day2, 4, "100", "102.5", "100", "102")])
        assert [s.kind for s in signals] == [SignalKind.ENTRY]

    def test_no_entry_after_the_cut_off_and_partial_bars_never_trigger(self) -> None:
        late = Harness("orb_v1", ORB, no_entries_after="09:40")
        assert late.feed([*orb_opening(), bar(DAY1, 5, "100", "102.5", "100", "102")]) == []
        partial = Harness("orb_v1", ORB)
        assert (
            partial.feed([*orb_opening(), bar(DAY1, 4, "100", "102.5", "100", "102", partial=True)])
            == []
        )

    def test_a_redelivered_bar_changes_nothing(self) -> None:
        h = Harness("orb_v1", ORB)
        breakout = bar(DAY1, 4, "100", "102.5", "100", "102")
        first = h.feed([*orb_opening(), breakout])
        assert len(first) == 1 and h.feed([breakout]) == []

    def test_parameters_are_validated(self) -> None:
        for bad in ({"stop_atr_mult": "0"}, {"min_range_bps": 500, "max_range_bps": 100}):
            with pytest.raises(Exception):  # noqa: B017
                Harness("orb_v1", {**ORB, **bad})


VWAP = {"entry_z": "1.5", "atr_period": 3, "rsi_period": 3, "rsi_extreme": 40, "stop_atr_mult": 1,
        "max_hold_bars": 4, "skip_bars": 3, "cooldown_bars": 2}  # fmt: skip


def vwap_day(drop_to: str = "96") -> list[Candle]:
    quiet = [bar(DAY1, i, "100", "100.4", "99.6", "100", volume=10_000) for i in range(6)]
    return [*quiet, bar(DAY1, 6, "100", "100", drop_to, drop_to, volume=100)]


class TestVwapReversion:
    def test_a_stretch_below_vwap_with_oversold_rsi_is_bought(self) -> None:
        h = Harness("vwap_reversion_v1", VWAP)
        (signal,) = h.feed(vwap_day())
        assert (signal.kind, signal.side) == (SignalKind.ENTRY, OrderSide.BUY)
        assert "ATRs from VWAP" in signal.reason

    def test_a_stretch_above_vwap_is_sold_short_unless_shorting_is_off(self) -> None:
        up = [*vwap_day()[:6], bar(DAY1, 6, "100", "104", "100", "104", volume=100)]
        (signal,) = Harness("vwap_reversion_v1", VWAP).feed(up)
        assert signal.side is OrderSide.SELL
        assert Harness("vwap_reversion_v1", {**VWAP, "allow_short": False}).feed(up) == []

    def test_no_entry_before_skip_bars(self) -> None:
        h = Harness("vwap_reversion_v1", {**VWAP, "skip_bars": 20})
        assert h.feed(vwap_day()) == []

    def test_it_exits_when_price_returns_to_vwap_and_then_waits_out_the_cooldown(self) -> None:
        h = Harness("vwap_reversion_v1", VWAP)
        (entry,) = h.feed(vwap_day())
        h.positions.set(ALPHA, entry.quantity, "96")
        (exit_,) = h.feed([bar(DAY1, 7, "96", "101", "96", "100.5", volume=100_000)])
        assert exit_.kind is SignalKind.EXIT and "VWAP" in exit_.reason
        h.positions.set(ALPHA, 0)
        assert h.feed([bar(DAY1, 8, "100", "100", "95", "95", volume=100)]) == []  # cooling down

    def test_a_stop_and_a_time_stop_both_exit(self) -> None:
        h = Harness("vwap_reversion_v1", {**VWAP, "stop_atr_mult": "0.5"})
        (entry,) = h.feed(vwap_day())
        h.positions.set(ALPHA, entry.quantity, "96")
        (stop,) = h.feed([bar(DAY1, 7, "96", "96", "90", "91", volume=100)])
        assert "stopped" in stop.reason
        slow = Harness("vwap_reversion_v1", VWAP)
        (entry,) = slow.feed(vwap_day())
        slow.positions.set(ALPHA, entry.quantity, "96")
        drift = [bar(DAY1, 7 + i, "96", "96.3", "95.9", "96.1", volume=100) for i in range(4)]
        out = slow.feed(drift)
        assert out and "held too long" in out[-1].reason

    def test_parameters_are_validated(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            Harness("vwap_reversion_v1", {**VWAP, "rsi_extreme": 60})


RSI = {
    "trend_ema": 20, "rsi_period": 2, "rsi_entry": 15, "rsi_exit": 60, "atr_period": 3,
    "stop_atr_mult": 2, "max_hold_bars": 5, "skip_bars": 3,
}  # fmt: skip


def climb(start: int, step: int, count: int = 30) -> list[Candle]:
    """`count` bars moving `step` a bar from `start`, all in one session."""
    out = []
    for i in range(count):
        price = start + step * i
        out.append(bar(DAY1, i, str(price), str(price + 0.5), str(price - 0.5), str(price)))
    return out


def uptrend_then_dip() -> list[Candle]:
    # Thirty bars up to 129, then a sharp drop to 122: RSI(2) ~12, still well above the 20-bar EMA.
    return [*climb(100, 1), bar(DAY1, 30, "129", "129", "122", "122")]


def downtrend_then_rally() -> list[Candle]:
    return [*climb(130, -1), bar(DAY1, 30, "101", "108", "101", "108")]


class TestRsiPullback:
    def test_a_sharp_pullback_in_an_uptrend_is_bought(self) -> None:
        (signal,) = Harness("rsi_pullback_v1", RSI).feed(uptrend_then_dip())
        assert signal.side is OrderSide.BUY and signal.kind is SignalKind.ENTRY

    def test_a_sharp_rally_in_a_downtrend_is_sold_short_unless_off(self) -> None:
        (signal,) = Harness("rsi_pullback_v1", RSI).feed(downtrend_then_rally())
        assert signal.side is OrderSide.SELL
        off = Harness("rsi_pullback_v1", {**RSI, "allow_short": False})
        assert off.feed(downtrend_then_rally()) == []

    def test_no_signal_early_in_the_day(self) -> None:
        early = Harness("rsi_pullback_v1", {**RSI, "skip_bars": 40})
        assert early.feed(uptrend_then_dip()) == []

    def test_a_dip_below_the_trend_is_not_bought(self) -> None:
        crash = [*climb(100, 1), bar(DAY1, 30, "129", "129", "90", "90")]  # far below the EMA
        assert Harness("rsi_pullback_v1", RSI).feed(crash) == []

    def test_it_exits_when_rsi_recovers(self) -> None:
        h = Harness("rsi_pullback_v1", RSI)
        (entry,) = h.feed(uptrend_then_dip())
        h.positions.set(ALPHA, entry.quantity, "122")
        (exit_,) = h.feed([bar(DAY1, 31, "122", "130", "122", "129")])
        assert "RSI recovered" in exit_.reason

    def test_a_stop_and_a_time_stop_exit(self) -> None:
        h = Harness("rsi_pullback_v1", {**RSI, "stop_atr_mult": "0.5"})
        (entry,) = h.feed(uptrend_then_dip())
        h.positions.set(ALPHA, entry.quantity, "122")
        (stop,) = h.feed([bar(DAY1, 31, "122", "122", "110", "111")])
        assert "stopped" in stop.reason
        slow = Harness("rsi_pullback_v1", RSI)
        (entry,) = slow.feed(uptrend_then_dip())
        slow.positions.set(ALPHA, entry.quantity, "122")
        flat = [bar(DAY1, 31 + i, "122", "122.1", "121.9", "122") for i in range(5)]
        out = slow.feed(flat)
        assert out and "held too long" in out[-1].reason

    def test_parameters_are_validated(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            Harness("rsi_pullback_v1", {**RSI, "rsi_exit": 40})


class TestWarmupAndDeterminism:
    def test_history_replays_without_signals_and_a_mid_day_start_sees_the_same_range(self) -> None:
        clock = FixedClock(DAY1 + timedelta(minutes=30))
        history = ClosedBarHistory(clock)
        for b in orb_opening():
            history.record(b)
        h = Harness("orb_v1", ORB)
        h.ctx = StrategyContext(
            run_id="run", config=h.config, clock=clock, logger=logging.getLogger("t"),
            history=history, positions=h.positions, rng=random.Random(0),
        )  # fmt: skip
        fresh = type(h.strategy)(h.config)
        fresh.initialize(h.ctx)
        assert fresh.generate_signal() is None  # warm-up never signals
        fresh.on_market_data(bar(DAY1, 4, "100", "102.5", "100", "102"))
        signal = fresh.generate_signal()
        assert signal is not None and "99..101" in signal.reason

    @pytest.mark.parametrize("name", ["orb_v1", "vwap_reversion_v1", "rsi_pullback_v1"])
    def test_the_same_bars_give_the_same_signals(self, name: str) -> None:
        params = {"orb_v1": ORB, "vwap_reversion_v1": VWAP, "rsi_pullback_v1": RSI}[name]
        bars = [*orb_opening(), *uptrend_then_dip()[4:], *vwap_day()[6:]]
        first = [(s.kind, s.side, s.limit_price) for s in Harness(name, params).feed(bars)]
        second = [(s.kind, s.side, s.limit_price) for s in Harness(name, params).feed(bars)]
        assert first == second and INSTRUMENT

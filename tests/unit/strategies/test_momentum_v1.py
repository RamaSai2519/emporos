"""momentum_v1 on series small enough to check by hand.

Main series, periods fast=2 slow=3 rsi=2 (alpha 2/3 and 1/2), RSI band [50, 100]:

    index  close   EMA2     EMA3     spread   RSI2    cross
      2     10     10.0     10.0      0.0     100      (first spread: nothing to compare with)
      3      9      9.33     9.5     -0.17      0      DOWN
      6     11     10.27     9.94    +0.33     86.96   UP    -> entry
      9      9      9.71     9.99    -0.28     18.09   DOWN  -> exit (only if long)
     12     11     10.29    10.00    +0.29     81.89   UP    -> entry

Each row was worked out with exact fractions, independently of the Decimal implementation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.builtin.momentum_v1 import MomentumParameters, MomentumV1
from emporos.strategies.config import StrategyParameters
from tests.support.strategies import (
    INSTRUMENT,
    OTHER_INSTRUMENT,
    RUN_ID,
    T0,
    BookPositions,
    changed,
    closes_to_bars,
    make_context,
    momentum_raw,
    replay,
    resolved,
    tick_at,
)

MAIN = "10 10 10 9 8 9 11 12 11 9 8 9 11 13".split()
QUANTITY = 4545  # floor(50000 / 11)


def _bars(closes: list[str] | None = None, **kwargs: Any) -> list[Candle]:
    return closes_to_bars(closes or MAIN, **kwargs)


async def _signals(
    raw: dict[str, Any] | None = None, bars: list[Candle] | None = None, **kwargs: Any
) -> list[Signal]:
    result = await replay(resolved(raw or momentum_raw()), bars or _bars(), **kwargs)
    assert not result.report.halted, result.report.halt_reason
    return result.signals


def _at(index: int) -> datetime:
    """The close time of the bar at `index` on the main series."""
    return T0 + timedelta(minutes=5 * (index + 1))


async def test_a_cross_up_with_rsi_in_the_band_enters_long() -> None:
    signals = await _signals()

    assert [(s.kind, s.ts) for s in signals] == [
        (SignalKind.ENTRY, _at(6)),
        (SignalKind.ENTRY, _at(12)),
    ]  # index 3's cross DOWN does nothing: the strategy is flat


async def test_the_entry_carries_everything_risk_and_execution_need() -> None:
    entry = (await _signals())[0]

    assert entry.strategy_run_id == RUN_ID and entry.instrument_id == INSTRUMENT
    assert entry.side is OrderSide.BUY and entry.order_type is OrderType.LIMIT
    assert entry.limit_price == Money.of("11") and entry.trigger_price is None
    assert entry.quantity == QUANTITY
    assert entry.reason == "EMA2 10.27 crossed above EMA3 9.94; RSI2 86.96"


async def test_a_cross_down_while_long_exits_the_whole_position() -> None:
    signals = await _signals(fills=True)

    assert [s.kind for s in signals] == [SignalKind.ENTRY, SignalKind.EXIT, SignalKind.ENTRY]
    exit_ = signals[1]
    assert exit_.side is OrderSide.SELL and exit_.order_type is OrderType.LIMIT
    assert exit_.quantity == QUANTITY and exit_.limit_price == Money.of("9")
    assert exit_.ts == _at(9)
    assert exit_.reason == "EMA2 9.71 crossed below EMA3 9.99"


async def test_no_signal_is_repeated_while_the_averages_stay_apart() -> None:
    """Edge-triggered: 8 more rising bars after the cross produce nothing."""
    closes = MAIN[:7] + [str(12 + i) for i in range(8)]
    assert len(await _signals(bars=_bars(closes))) == 1


async def test_only_the_rsi_band_lets_a_cross_through() -> None:
    assert [s.ts for s in await _signals(momentum_raw(rsi_entry_max=85))] == [_at(12)]  # 86.96 out
    assert await _signals(momentum_raw(rsi_entry_min=90)) == []  # 86.96 and 81.89 both below


async def test_it_does_not_enter_a_position_it_already_holds() -> None:
    held = BookPositions()
    held.set(INSTRUMENT, 50)

    signals = await _signals(positions=held)

    assert {s.kind for s in signals} == {SignalKind.EXIT}  # it may leave; it never adds


async def test_it_does_not_exit_what_it_does_not_hold() -> None:
    assert SignalKind.EXIT not in {s.kind for s in await _signals()}


async def test_warm_up_bars_never_produce_a_signal() -> None:
    quick = "10 10 10 12 14 14".split()  # slow=3: the cross at index 3 is the first possible
    assert [s.ts for s in await _signals(bars=_bars(quick))] == [_at(3)]
    # the same cross with slow=5 lands inside the warm-up: nothing
    assert await _signals(momentum_raw(slow_ema=5), bars=_bars(quick)) == []


async def test_a_partial_bar_feeds_the_indicators_but_never_triggers_a_signal() -> None:
    bars = _bars()
    bars[6] = replace(bars[6], partial=True)

    signals = await _signals(bars=bars)

    assert [s.ts for s in signals] == [_at(12)]  # index 6 skipped; the series is otherwise intact


async def test_a_redelivered_or_stale_bar_does_not_advance_the_indicators() -> None:
    bars = _bars()
    noisy = [*bars[:7], bars[6], bars[5], *bars[7:]]  # bar 6 again, then an older bar 5

    assert await _signals(bars=noisy) == await _signals(bars=bars)


async def test_ticks_other_timeframes_and_foreign_instruments_are_ignored() -> None:
    clean = await _signals()
    noise = [
        tick_at(seconds=3),
        *closes_to_bars(["500", "1", "500"], timeframe=Timeframe.M15),
        *closes_to_bars(["500", "1", "500", "1"], instrument_id="NSE:9999"),
    ]

    assert await _signals(bars=[*noise, *_bars()]) == clean


async def test_each_instrument_has_its_own_state() -> None:
    flat = closes_to_bars(["10"] * len(MAIN), instrument_id=OTHER_INSTRUMENT)
    interleaved = [b for pair in zip(_bars(), flat, strict=True) for b in pair]

    signals = await _signals(bars=interleaved)

    assert {s.instrument_id for s in signals} == {INSTRUMENT}
    assert [s.ts for s in signals] == [_at(6), _at(12)]


async def test_two_instruments_on_the_same_path_each_signal_independently() -> None:
    both = [*_bars(), *closes_to_bars(MAIN, instrument_id=OTHER_INSTRUMENT)]
    signals = await _signals(bars=sorted(both, key=lambda b: (b.ts, b.instrument_id)))
    assert {s.instrument_id for s in signals} == {INSTRUMENT, OTHER_INSTRUMENT}
    assert len(signals) == 4


async def test_the_size_is_what_max_position_value_buys_at_the_close() -> None:
    raw = changed(momentum_raw(), "risk.max_position_value", 1000)  # 1000 / 11 = 90.9 -> 90
    assert [s.quantity for s in await _signals(raw)] == [90, 90]


async def test_a_limit_that_buys_less_than_one_share_emits_nothing() -> None:
    raw = changed(momentum_raw(), "risk.max_position_value", 10)  # 10 / 11 < 1
    assert await _signals(raw) == []


# 09:15 IST is 03:45 UTC; 15:00 IST is 09:30 UTC.
@pytest.mark.parametrize(
    ("start_ist", "expected"),
    [
        ("14:20", [6]),  # index 6 closes 14:55 (allowed); index 12 closes 15:25 (not)
        ("14:25", []),  # index 6 closes exactly 15:00: entries stop AT the cut-off
        ("14:40", []),
    ],
)
async def test_no_new_entries_at_or_after_the_session_cut_off(
    start_ist: str, expected: list[int]
) -> None:
    hour, minute = map(int, start_ist.split(":"))
    start = T0.replace(hour=hour, minute=minute) - timedelta(hours=5, minutes=30)
    signals = await _signals(bars=_bars(start=start))

    assert [s.ts for s in signals] == [start + timedelta(minutes=5 * (i + 1)) for i in expected]


async def test_an_exit_is_never_blocked_by_the_cut_off() -> None:
    held = BookPositions()
    held.set(INSTRUMENT, 100)
    start = T0.replace(hour=9, minute=10)  # 14:40 IST: every bar closes after the 15:00 cut-off

    signals = await _signals(bars=_bars(start=start), positions=held)

    # Both cross-downs exit (nothing fills here, so it is still long at the second); the cross-up
    # in between cannot enter: it is past the cut-off and the strategy already holds the stock.
    assert [(s.kind, s.quantity, s.ts) for s in signals] == [
        (SignalKind.EXIT, 100, start + timedelta(minutes=20)),
        (SignalKind.EXIT, 100, start + timedelta(minutes=50)),
    ]


async def test_warming_up_from_history_matches_having_seen_every_bar() -> None:
    bars = _bars()
    full = await _signals(bars=bars)

    resumed = await _signals(bars=bars[6:], warmup=bars[:6])

    assert resumed == full


async def test_history_recorded_ahead_of_the_clock_cannot_leak_into_the_warm_up() -> None:
    """Bars 0-9 are all IN the store, but the clock stands at bar 5's close: only 0-5 may warm the
    strategy. If bar 6-9 leaked in, bar 6 would look already-seen and the entry would vanish."""
    bars = _bars()

    signals = await _signals(bars=bars[6:], warmup=bars[:10], warmup_clock=bars[5].closes_at)

    assert signals == await _signals(bars=bars)


def test_the_parameters_are_checked_for_consistency() -> None:
    good = {"fast_ema": 20, "slow_ema": 50, "rsi_period": 14}
    parameters = MomentumParameters.model_validate(good)
    assert (parameters.rsi_entry_min, parameters.rsi_entry_max) == (50, 70)  # defaults
    for bad in [
        {"fast_ema": 50, "slow_ema": 50},
        {"fast_ema": 60},
        {"rsi_entry_min": 70},
        {"rsi_entry_min": 80, "rsi_entry_max": 70},
        {"rsi_entry_max": 101},
        {"rsi_entry_min": -1},
        {"fast_ema": 0},
        {"fast_ema": 2.0},
        {"fast_ema": True},
        {"rsi_period": "14"},
        {"unknown": 1},
        {"rsi_entry_min": 55.5},
    ]:
        with pytest.raises(ValidationError):
            MomentumParameters.model_validate(good | bad)


def test_it_refuses_another_strategys_parameters() -> None:
    wrong = resolved(momentum_raw()).model_copy(update={"parameters": StrategyParameters()})
    with pytest.raises(TypeError, match="MomentumParameters"):
        MomentumV1(wrong)


def test_a_signal_path_used_before_initialize_fails_loudly() -> None:
    strategy = MomentumV1(resolved(momentum_raw()))
    bars = _bars()
    for bar in bars[:3]:  # fills the warm-up; no cross yet
        strategy.on_market_data(bar)

    with pytest.raises(RuntimeError, match="initialize"):
        strategy.on_market_data(bars[3])  # the cross DOWN needs the context


def test_a_strategy_initialised_on_an_empty_history_has_nothing_to_say() -> None:
    strategy = MomentumV1(resolved(momentum_raw()))
    strategy.initialize(make_context(FixedClock(T0), config=strategy.config))
    assert strategy.generate_signal() is None

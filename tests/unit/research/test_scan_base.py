"""EM-191 F3b: the order path every scan shares, checked against scripted rules.

Each case pins one thing the real engine does (`backtest.broker`, `square_off`,
`domain.marketable`): a signal at a bar's close trades on a LATER bar, only strictly through its
limit, only if the bar's volume has room; the session square-off; the broker's forced close. The
real-engine comparison is `test_scan_parity.py`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    ExitIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)

INSTRUMENT = "NSE:2885"
OPEN_UTC = datetime(2026, 9, 21, 3, 45, tzinfo=UTC)  # 09:15 IST


def bar(
    index: int, close: str, *, low: str | None = None, high: str | None = None,
    volume: int = 1_000_000, partial: bool = False, day_offset: int = 0,
) -> Candle:  # fmt: skip
    """The `index`th 5-minute bar of a session (0 = 09:15 IST). Open and close are `close`; the
    range is a rupee either side unless a test sets it."""
    price = Decimal(close)
    return Candle(
        INSTRUMENT, Timeframe.M5, OPEN_UTC + timedelta(days=day_offset, minutes=5 * index),
        Money.of(price), Money.of(Decimal(high) if high else price + 1),
        Money.of(Decimal(low) if low else price - 1), Money.of(price), volume, partial,
    )  # fmt: skip


class Script:
    """`ScanRules` that say what a test tells them to, by bar index, and record what they saw."""

    def __init__(self, script: dict[int, ScanIntent]) -> None:
        self._script = script
        self._seen = 0
        self.held: list[OrderSide | None] = []
        self.can_afford: list[bool] = []

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        index = self._seen
        self._seen += 1
        self.held.append(held)
        self.can_afford.append(can_afford)
        return self._script.get(index)


def scan_of(script: Script, **overrides: object) -> IntradayScan:
    execution = ScanExecution(Decimal(25_000), tick_size=Decimal("0.1"), **overrides)  # type: ignore[arg-type]
    return IntradayScan("scripted", lambda: script, execution)


def run(
    script: Script, bars: Sequence[Candle], **overrides: object
) -> list[tuple[str, Decimal, Decimal]]:
    trades = scan_of(script, **overrides).scan(INSTRUMENT, bars)
    return [(t.side.value, t.entry_price.amount, t.exit_price.amount) for t in trades]


LONG = EntryIntent(OrderSide.BUY)
SHORT = EntryIntent(OrderSide.SELL)
EXIT = ExitIntent()
D = Decimal


def test_a_round_trip_is_priced_at_the_signal_bars_close_before_slippage() -> None:
    # entry signalled on bar 1 (close 101), filled on bar 2; exit signalled on bar 3 (close 103),
    # filled on bar 4. Each fill lands before the strategy looks at that bar.
    bars = [
        bar(0, "100"),
        bar(1, "101"),
        bar(2, "102"),
        bar(3, "103"),
        bar(4, "104"),
        bar(5, "104"),
    ]
    script = Script({1: LONG, 3: EXIT})

    assert run(script, bars) == [("BUY", D(101), D(103))]
    assert script.held == [None, None, OrderSide.BUY, OrderSide.BUY, None, None]


def test_the_order_cannot_trade_on_the_bar_that_signalled_it() -> None:
    # bar 1 signals a buy at 101 (limit 101.1) and itself dips to 90: that must not fill it.
    bars = [
        bar(0, "100"),
        bar(1, "101", low="90"),
        bar(2, "102", low="101.5"),  # above the limit: no fill
        bar(3, "102", low="100"),  # through it: fills
        bar(4, "102"),
    ]
    script = Script({1: LONG})

    run(script, bars)

    assert script.held == [None, None, None, OrderSide.BUY, OrderSide.BUY]


def test_a_bar_that_only_touches_the_limit_does_not_fill_it() -> None:
    # a buy signalled at 100 has limit 100.05 -> 100.1 on a 0.1 tick
    touching = Script({0: LONG})
    run(touching, [bar(0, "100"), bar(1, "101", low="100.1"), bar(2, "101", low="100.1")])
    through = Script({0: LONG})
    run(through, [bar(0, "100"), bar(1, "101", low="100.0")])

    assert touching.held == [None, None, None]
    assert through.held == [None, OrderSide.BUY]


def test_a_short_needs_the_high_through_its_limit() -> None:
    # a sell signalled at 100 has limit 99.95 -> 99.9
    miss, hit = Script({0: SHORT}), Script({0: SHORT})
    run(miss, [bar(0, "100"), bar(1, "99", high="99.9")])
    run(hit, [bar(0, "100"), bar(1, "100", high="100")])

    assert miss.held == [None, None]
    assert hit.held == [None, OrderSide.SELL]


def test_a_short_trade_reports_the_short_side() -> None:
    bars = [bar(0, "100"), bar(1, "100"), bar(2, "99"), bar(3, "99"), bar(4, "99")]

    assert run(Script({0: SHORT, 2: EXIT}), bars) == [("SELL", D(100), D(99))]


def test_a_bar_with_no_room_in_its_volume_does_not_fill() -> None:
    # 25,000 / 100 = 250 shares; 10% of 2,000 is 200, so the order cannot be taken whole
    thin, roomy = Script({0: LONG}), Script({0: LONG})
    run(thin, [bar(0, "100"), bar(1, "100", volume=2_000), bar(2, "100", volume=2_000)])
    run(roomy, [bar(0, "100"), bar(1, "100", volume=2_500)])

    assert thin.held == [None, None, None]
    assert roomy.held == [None, OrderSide.BUY]


def test_a_partial_bar_fills_nothing() -> None:
    script = Script({0: LONG})

    run(script, [bar(0, "100"), bar(1, "100", partial=True), bar(2, "100")])

    assert script.held == [None, None, OrderSide.BUY]


def _late_session(entry_close: str, *, until: int) -> list[Candle]:
    """Flat at 100 to the 15:05 bar (index 70), then `entry_close` for the rest through `until`."""
    flat = [bar(i, "100") for i in range(71)]
    return flat + [bar(i, entry_close) for i in range(71, until + 1)]


def test_a_position_still_open_at_the_square_off_is_closed_by_the_session() -> None:
    # entry signalled on bar 70 fills on bar 71 (index 71 is the 15:10 bar, closing at 15:15 = the
    # square-off). The session signals the exit at bar 71's close, 105; bar 72 fills it.
    bars = _late_session("105", until=72)
    bars[71] = bar(71, "105", low="99")
    script = Script({70: LONG})

    assert run(script, bars) == [("BUY", D(100), D(105))]
    assert script.held[71] is OrderSide.BUY


def test_the_strategy_is_refused_once_the_session_owns_the_days_close() -> None:
    bars = _late_session("105", until=74)
    bars[71] = bar(71, "105", low="99")
    script = Script({70: LONG, 73: LONG})  # a second entry after the session has closed us out

    assert len(run(script, bars)) == 1


def test_what_is_still_open_at_the_end_of_the_day_is_closed_at_the_last_price_against_us() -> None:
    # no bar after the 15:10 one: the square-off exit cannot fill, so the broker closes at the last
    # price, 10 bps against a long. The screener adds 5 bps itself, so the scan hands over 5.
    bars = _late_session("100", until=71)
    bars[71] = bar(71, "100", low="99")

    assert run(Script({70: LONG}), bars) == [("BUY", D(100), D("99.95"))]


def test_a_short_is_closed_by_the_broker_above_the_last_price() -> None:
    bars = _late_session("100", until=71)

    assert run(Script({70: SHORT}), bars) == [("SELL", D(100), D("100.05"))]


def test_days_are_independent() -> None:
    day1 = [bar(i, "100") for i in range(4)]
    day2 = [bar(i, "100", day_offset=1) for i in range(4)]
    script = Script({0: LONG, 4: LONG})  # one entry on each day

    trades = scan_of(script).scan(INSTRUMENT, day1 + day2)

    assert [t.day.isoformat() for t in trades] == ["2026-09-21", "2026-09-22"]


def test_a_name_too_dear_for_one_share_is_never_entered_and_the_rules_are_told() -> None:
    dear = [bar(0, "30000"), bar(1, "30000")]
    script = Script({0: LONG})

    assert run(script, dear) == []
    assert script.can_afford == [False, False]


def test_the_limit_is_marketable_and_rounded_to_the_tick() -> None:
    execution = ScanExecution(
        Decimal(25_000), limit_buffer_bps=Decimal(5), tick_size=Decimal("0.05")
    )

    assert execution.limit_for(OrderSide.BUY, Decimal("100.02")) == Decimal("100.10")  # 100.07 up
    assert execution.limit_for(OrderSide.SELL, Decimal("100.02")) == Decimal("99.95")  # 99.97 down


@pytest.mark.parametrize(
    "kwargs",
    [
        {"position_value": Decimal(0)},
        {"position_value": Decimal(1), "tick_size": Decimal(0)},
        {"position_value": Decimal(1), "participation": Decimal(0)},
        {"position_value": Decimal(1), "participation": Decimal("1.1")},
        {"position_value": Decimal(1), "limit_buffer_bps": Decimal(-1)},
        {"position_value": Decimal(1), "forced_close_penalty_bps": Decimal(-1)},
    ],
)
def test_impossible_assumptions_are_refused(kwargs: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        ScanExecution(**kwargs)


def test_the_entry_window_is_exclusive_of_a_bar_closing_at_the_cutoff() -> None:
    cutoff = time(14, 45)

    assert entries_open(bar(64, "100"), cutoff)  # closes 14:40
    assert not entries_open(bar(65, "100"), cutoff)  # closes 14:45 exactly
    assert not entries_open(bar(69, "100"), cutoff)

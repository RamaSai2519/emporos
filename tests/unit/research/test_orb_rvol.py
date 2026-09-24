"""EM-191, cell L3-orb-high-rvol-wide-range: every rule of the scan, pinned.

The scan is not parity-proven (no engine strategy), so its screens are advisory and these tests are
what stands behind it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.orb_rvol import (
    MIN_HISTORY,
    OrbExit,
    OrbRvolParameters,
    declared_arms,
    orb_rvol_scan,
)
from emporos.research.scans.proven import is_parity_proven

INSTRUMENT = "NSE:1"
EXECUTION = ScanExecution(Decimal(25_000))
FIRST = date(2026, 1, 5)


def session(
    day: date, *, range_volume: int = 100_000, half_width: str = "0.5", breakout: str | None = None,
    breakout_bar: int = 8, skip_bar: int | None = None,
) -> list[Candle]:  # fmt: skip
    """A day of 5m bars from 09:15 IST at a flat 100. The six opening-range bars carry
    `range_volume` in total and span 100 +- `half_width`; bar `breakout_bar` (1-based) closes at
    `breakout` when given. Later bars close at the breakout price so the trade can fill and exit."""
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)
    held = Decimal(breakout) if breakout is not None else Decimal(100)
    out: list[Candle] = []
    for i in range(75):
        if i + 1 == skip_bar:
            continue
        in_range = i < 6
        close = Decimal(100) if i + 1 < breakout_bar else held
        high = Decimal(100) + Decimal(half_width) if in_range else max(close, Decimal(100)) + 1
        low = Decimal(100) - Decimal(half_width) if in_range else min(close, Decimal(100)) - 1
        out.append(
            Candle(
                INSTRUMENT,
                Timeframe.M5,
                start + timedelta(minutes=5 * i),
                Money.of(100),
                Money.of(high),
                Money.of(low),
                Money.of(close),
                range_volume // 6 if in_range else 1_000_000,
            )  # fmt: skip
        )
    return out


def history(days: int = MIN_HISTORY) -> list[Candle]:
    bars: list[Candle] = []
    for n in range(days):
        bars += session(FIRST + timedelta(days=n))
    return bars


def params(rvol: str = "2.0", width: str = "50", exit_: OrbExit = OrbExit.CLOSE):  # type: ignore[no-untyped-def]
    return OrbRvolParameters(Decimal(rvol), Decimal(width), exit_)


def trades(parameters: OrbRvolParameters, bars: list[Candle]):  # type: ignore[no-untyped-def]
    return orb_rvol_scan(parameters, EXECUTION).scan(INSTRUMENT, bars)


def hot_day(**kwargs: object) -> list[Candle]:
    day = FIRST + timedelta(days=MIN_HISTORY)
    return session(day, **kwargs)  # type: ignore[arg-type]


class TestQualification:
    def test_high_volume_and_wide_range_breakout_up_buys_and_holds_to_the_close(self) -> None:
        (trade,) = trades(params(), history() + hot_day(range_volume=300_000, breakout="103"))

        assert trade.side is OrderSide.BUY
        assert trade.entry_price.amount == Decimal(103)

    def test_a_breakout_down_sells(self) -> None:
        (trade,) = trades(params(), history() + hot_day(range_volume=300_000, breakout="97"))

        assert trade.side is OrderSide.SELL

    def test_volume_below_the_multiple_does_not_trade(self) -> None:
        assert (
            trades(params("2.0"), history() + hot_day(range_volume=199_000, breakout="103")) == []
        )

    def test_volume_exactly_at_the_multiple_trades(self) -> None:
        assert (
            len(trades(params("2.0"), history() + hot_day(range_volume=200_000, breakout="103")))
            == 1
        )

    def test_a_range_narrower_than_the_floor_does_not_trade(self) -> None:
        # +-0.2 on 100 is 40 bps wide, under a 50 bps floor
        bars = history() + hot_day(range_volume=300_000, half_width="0.2", breakout="103")
        assert trades(params(width="50"), bars) == []

    def test_a_close_inside_the_buffer_is_not_a_breakout(self) -> None:
        assert trades(params(), history() + hot_day(range_volume=300_000, breakout="100.52")) == []

    def test_no_breakout_means_no_trade(self) -> None:
        assert trades(params(), history() + hot_day(range_volume=300_000)) == []


class TestHistory:
    def test_fewer_than_the_minimum_prior_sessions_does_not_trade(self) -> None:
        bars = history(MIN_HISTORY - 1)
        day = FIRST + timedelta(days=MIN_HISTORY - 1)
        assert trades(params(), bars + session(day, range_volume=300_000, breakout="103")) == []

    def test_a_day_with_a_missing_range_bar_is_skipped_and_not_recorded(self) -> None:
        # ten sessions but one has a hole in its opening range: only nine count, so no trade
        bars = history(MIN_HISTORY - 1)
        bars += session(FIRST + timedelta(days=MIN_HISTORY - 1), skip_bar=3)
        bars += hot_day(range_volume=300_000, breakout="103")
        assert trades(params(), bars) == []

    def test_a_missing_range_bar_skips_that_day_itself(self) -> None:
        bars = history() + hot_day(range_volume=300_000, breakout="103", skip_bar=2)
        assert trades(params(), bars) == []

    def test_the_days_own_volume_is_not_in_its_own_baseline(self) -> None:
        # ten history days of 100k then a 150k day: rvol is 1.5, not diluted by itself
        bars = history() + hot_day(range_volume=150_000, breakout="103")
        assert len(trades(params("1.5"), bars)) == 1
        assert trades(params("1.6"), bars) == []


class TestExit:
    def test_the_midday_time_stop_exits_at_1230_and_close_holds_on(self) -> None:
        bars = history() + hot_day(range_volume=300_000, breakout="103")
        day = bars[-75:]

        def bar(b: Candle, price: str, high: str, low: str) -> Candle:
            return Candle(INSTRUMENT, Timeframe.M5, b.ts, Money.of(price), Money.of(high),
                          Money.of(low), Money.of(price), 1_000_000)  # fmt: skip

        # the bar closing at 12:30 (it starts 12:25 IST = 06:55 UTC) closes at 108; the price
        # then falls to 90 for the rest of the day
        for i, b in enumerate(day):
            if (b.ts.hour, b.ts.minute) == (6, 55):
                day[i] = bar(b, "108", "110", "107")
            elif (b.ts.hour, b.ts.minute) > (6, 55):
                day[i] = bar(b, "90", "110" if (b.ts.hour, b.ts.minute) == (7, 0) else "91", "89")
        full = bars[:-75] + day

        (midday,) = trades(params(exit_=OrbExit.MIDDAY), full)
        (close,) = trades(params(exit_=OrbExit.CLOSE), full)

        assert midday.exit_price.amount == Decimal(108)
        assert close.exit_price.amount == Decimal(90)

    def test_the_time_stop_takes_no_second_entry(self) -> None:
        bars = history() + hot_day(range_volume=300_000, breakout="103")
        assert len(trades(params(exit_=OrbExit.MIDDAY), bars)) == 1


class TestGrid:
    def test_declared_arms_are_the_full_product(self) -> None:
        grid = {"rvol_min": ("1.5", "2.0", "3.0"), "or_width_min_bps": ("50", "100"),
                "exit": ("close", "1230")}  # fmt: skip
        assert len(declared_arms(grid)) == 12

    def test_an_undeclared_parameter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"rvol_min": ("2",)})

    def test_the_scan_is_advisory(self) -> None:
        assert not is_parity_proven("orb_rvol_wide_range")

    def test_a_point_round_trips(self) -> None:
        p = params("1.5", "100", OrbExit.MIDDAY)
        assert OrbRvolParameters.from_point(p.as_point()) == p

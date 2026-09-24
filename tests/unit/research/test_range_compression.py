"""EM-191, cell L4-nr7-inside-day-breakout: every rule of the scan, pinned.

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
from emporos.research.scans.proven import is_parity_proven
from emporos.research.scans.range_compression import (
    Compression,
    CompressionParameters,
    declared_arms,
    range_compression_scan,
)

INSTRUMENT = "NSE:1"
EXECUTION = ScanExecution(Decimal(25_000))
START = date(2026, 1, 5)


def candle(day: date, i: int, o: str, h: str, low: str, c: str) -> Candle:
    ts = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC) + timedelta(minutes=5 * i)
    return Candle(
        INSTRUMENT, Timeframe.M5, ts, Money.of(o), Money.of(h), Money.of(low), Money.of(c),
        1_000_000,
    )  # fmt: skip


def quiet(n: int, high: str, low: str, bars: int = 75) -> list[Candle]:
    """Session `n` (a date offset from START): closes at 100 all day; bar 1 spans high..low, which
    sets the session's high and low."""
    day = START + timedelta(days=n)
    out = [candle(day, 0, "100", high, low, "100")]
    out += [candle(day, i, "100", "100.05", "99.95", "100") for i in range(1, bars)]
    return out


def breakout_day(n: int, price: str, at_bar: int = 3) -> list[Candle]:
    """A session that sits at 100 until bar `at_bar` (1-based), then closes at `price` for the day.
    Bars carry a rupee either side so an order placed on the break can trade through."""
    day = START + timedelta(days=n)
    out: list[Candle] = []
    p = Decimal(price)
    for i in range(75):
        c = "100" if i + 1 < at_bar else price
        hi = str(max(Decimal(c), Decimal(100)) + 1)
        lo = str(min(Decimal(c), Decimal(100)) - 1)
        out.append(candle(day, i, "100" if i == 0 else c, hi if i + 1 >= at_bar else "100.05",
                          lo if i + 1 >= at_bar else "99.95", c))  # fmt: skip
    assert p == Decimal(price)
    return out


def params(condition: Compression = Compression.NR7, buffer: str = "5") -> CompressionParameters:
    return CompressionParameters(condition, Decimal(buffer))


def trades(p: CompressionParameters, bars: list[Candle]):  # type: ignore[no-untyped-def]
    return range_compression_scan(p, EXECUTION).scan(INSTRUMENT, bars)


def wide_days(count: int, first: int = 0) -> list[Candle]:
    bars: list[Candle] = []
    for n in range(first, first + count):
        bars += quiet(n, "103", "97")  # a range of 6
    return bars


class TestNr7:
    def test_a_break_above_a_narrowest_of_seven_day_buys_and_holds_to_the_close(self) -> None:
        bars = wide_days(6) + quiet(6, "101", "99") + breakout_day(7, "102")

        (trade,) = trades(params(), bars)

        assert (trade.side, trade.day) == (OrderSide.BUY, START + timedelta(days=7))
        assert trade.entry_price.amount == Decimal(102)

    def test_a_break_below_sells(self) -> None:
        bars = wide_days(6) + quiet(6, "101", "99") + breakout_day(7, "98")

        (trade,) = trades(params(), bars)

        assert trade.side is OrderSide.SELL

    def test_a_day_that_is_not_the_narrowest_of_seven_does_not_trade(self) -> None:
        bars = wide_days(6) + quiet(6, "103", "97") + breakout_day(7, "104")  # ties: not strict
        assert trades(params(), bars) == []

    def test_fewer_than_seven_sessions_does_not_trade(self) -> None:
        bars = wide_days(5) + quiet(5, "101", "99") + breakout_day(6, "102")
        assert trades(params(), bars) == []

    def test_nr4_needs_only_four_sessions(self) -> None:
        bars = wide_days(3) + quiet(3, "101", "99") + breakout_day(4, "102")
        assert len(trades(params(Compression.NR4), bars)) == 1
        assert trades(params(Compression.NR7), bars) == []


class TestInside:
    def test_a_session_inside_the_one_before_it_arms_the_next_day(self) -> None:
        bars = quiet(0, "104", "96") + quiet(1, "102", "98") + breakout_day(2, "103")
        assert len(trades(params(Compression.INSIDE), bars)) == 1

    def test_a_session_that_pokes_out_of_the_one_before_it_does_not(self) -> None:
        bars = quiet(0, "104", "96") + quiet(1, "105", "98") + breakout_day(2, "103")
        assert trades(params(Compression.INSIDE), bars) == []

    def test_a_shared_extreme_is_not_inside(self) -> None:
        bars = quiet(0, "104", "96") + quiet(1, "104", "98") + breakout_day(2, "106")
        assert trades(params(Compression.INSIDE), bars) == []


class TestBreak:
    def test_a_close_inside_the_buffer_is_not_a_break(self) -> None:
        # the previous high is 101; 5 bps above it is 101.0505
        bars = wide_days(6) + quiet(6, "101", "99") + breakout_day(7, "101.04")
        assert trades(params(), bars) == []

    def test_a_wider_buffer_needs_a_bigger_break(self) -> None:
        bars = wide_days(6) + quiet(6, "101", "99") + breakout_day(7, "101.2")
        assert len(trades(params(buffer="5"), bars)) == 1
        assert trades(params(buffer="25"), bars) == []

    def test_one_trade_per_day(self) -> None:
        assert (
            len(trades(params(), wide_days(6) + quiet(6, "101", "99") + breakout_day(7, "102")))
            == 1
        )


class TestHoles:
    def test_a_short_session_breaks_the_run(self) -> None:
        bars = wide_days(3) + quiet(3, "103", "97", bars=40) + wide_days(2, first=4)
        bars += quiet(6, "101", "99") + breakout_day(7, "102")
        assert trades(params(), bars) == []  # only four whole sessions since the hole

    def test_a_short_session_is_not_itself_the_narrow_day(self) -> None:
        bars = wide_days(6) + quiet(6, "101", "99", bars=40) + breakout_day(7, "102")
        assert trades(params(), bars) == []


class TestGrid:
    def test_declared_arms_are_the_full_product(self) -> None:
        grid = {"condition": ("nr4", "nr7", "inside"), "buffer_bps": ("5", "25")}
        assert len(declared_arms(grid)) == 6

    def test_an_undeclared_parameter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"condition": ("nr7",)})

    def test_the_scan_is_advisory(self) -> None:
        assert not is_parity_proven("range_compression_breakout")

    def test_a_point_round_trips(self) -> None:
        p = params(Compression.INSIDE, "25")
        assert CompressionParameters.from_point(p.as_point()) == p

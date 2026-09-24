"""EM-191, cell L3-first-hour-shock-reversal: every rule of the scan, pinned.

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
from emporos.research.scans.shock_reversal import (
    ShockReversalParameters,
    declared_arms,
    shock_reversal_scan,
)

INSTRUMENT = "NSE:1"
FRIDAY, MONDAY = date(2026, 1, 2), date(2026, 1, 5)
EXECUTION = ScanExecution(Decimal(25_000))


def session(
    day: date, *, open_: str, at_hour: str, close: str, skip_bar: int | None = None,
) -> list[Candle]:  # fmt: skip
    """A session of 5m bars from 09:15 IST. Bar 1 opens at `open_`; bars 2..11 close at `open_`;
    bar 12 (starts 10:10, the end of the first hour) closes at `at_hour`; bar 13 opens where bar 12
    closed (so an order placed at 10:15 can trade); later bars close at `close`. A rupee of range
    either side lets orders trade through."""
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(75):
        if i + 1 == skip_bar:
            continue
        closing = Decimal(open_ if i < 11 else at_hour if i == 11 else close)
        opening = Decimal(open_) if i == 0 else Decimal(at_hour) if i == 12 else closing
        out.append(
            Candle(
                INSTRUMENT,
                Timeframe.M5,
                start + timedelta(minutes=5 * i),
                Money.of(opening),
                Money.of(max(opening, closing) + 1),
                Money.of(min(opening, closing) - 1),
                Money.of(closing),
                1_000_000,
            )  # fmt: skip
        )
    return out


def params(gap: str = "1.0", retrace: str = "0.5") -> ShockReversalParameters:
    return ShockReversalParameters(Decimal(gap), Decimal(retrace))


def trades(parameters: ShockReversalParameters, bars: list[Candle]):  # type: ignore[no-untyped-def]
    return shock_reversal_scan(parameters, EXECUTION).scan(INSTRUMENT, bars)


def gap_up(at_hour: str = "101", open_: str = "102", close: str = "99") -> list[Candle]:
    """Friday closes 100; Monday gaps to `open_` (default +2%)."""
    return session(FRIDAY, open_="100", at_hour="100", close="100") + session(
        MONDAY, open_=open_, at_hour=at_hour, close=close
    )


class TestRetrace:
    def test_a_gap_up_that_gave_back_half_is_sold_at_the_first_hours_close(self) -> None:
        (trade,) = trades(params(), gap_up(at_hour="101"))  # 1 of 2 given back: retrace 0.5

        assert (trade.side, trade.day) == (OrderSide.SELL, MONDAY)
        assert trade.entry_price.amount == Decimal(101)
        assert trade.exit_price.amount == Decimal(99)  # the 15:15 square-off bar's close

    def test_a_gap_down_is_mirrored(self) -> None:
        bars = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="98", at_hour="99", close="101"
        )

        (trade,) = trades(params(), bars)

        assert trade.side is OrderSide.BUY

    def test_less_than_the_retrace_floor_does_not_trade(self) -> None:
        assert trades(params(retrace="0.5"), gap_up(at_hour="101.2")) == []  # retrace 0.4

    def test_a_price_still_at_the_open_or_beyond_it_does_not_trade(self) -> None:
        assert trades(params(), gap_up(at_hour="102")) == []  # retrace 0
        assert trades(params(), gap_up(at_hour="104")) == []  # ran further: negative retrace

    def test_a_price_through_the_previous_close_still_counts(self) -> None:
        (trade,) = trades(params(), gap_up(at_hour="99"))  # retrace 1.5

        assert trade.side is OrderSide.SELL


class TestGap:
    def test_a_gap_below_the_threshold_does_not_trade(self) -> None:
        assert trades(params("1.0"), gap_up(open_="100.9", at_hour="100")) == []

    def test_a_gap_exactly_at_the_threshold_trades(self) -> None:
        assert len(trades(params("1.0"), gap_up(open_="101", at_hour="100"))) == 1

    def test_a_bigger_threshold_needs_a_bigger_gap(self) -> None:
        assert trades(params("3.0"), gap_up(open_="102", at_hour="101")) == []

    def test_the_first_day_has_no_previous_close(self) -> None:
        assert trades(params(), session(MONDAY, open_="102", at_hour="101", close="99")) == []


class TestMissingBars:
    def test_a_missing_opening_bar_skips_the_day(self) -> None:
        bars = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="102", at_hour="101", close="99", skip_bar=1
        )
        assert trades(params(), bars) == []

    def test_a_missing_decision_bar_skips_the_day(self) -> None:
        bars = session(FRIDAY, open_="100", at_hour="100", close="100") + session(
            MONDAY, open_="102", at_hour="101", close="99", skip_bar=12
        )
        assert trades(params(), bars) == []

    def test_one_trade_per_day(self) -> None:
        assert len(trades(params(), gap_up())) == 1


class TestGrid:
    def test_declared_arms_are_the_full_product(self) -> None:
        grid = {"gap_threshold_pct": ("1.0", "1.5", "2.0"), "retrace_min": ("0.25", "0.5")}
        assert len(declared_arms(grid)) == 6

    def test_an_undeclared_parameter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"gap_threshold_pct": ("1",)})

    def test_non_positive_parameters_are_refused(self) -> None:
        with pytest.raises(ValueError):
            params(retrace="0")

    def test_the_scan_is_advisory(self) -> None:
        assert not is_parity_proven("first_hour_shock_reversal")

    def test_a_point_round_trips(self) -> None:
        assert ShockReversalParameters.from_point(params("1.5", "0.25").as_point()) == params(
            "1.5", "0.25"
        )

"""EM-191, cell L3-raw-gap-hold-to-close: every rule of the raw-gap scan, pinned.

The scan is not parity-proven (no engine strategy), so its screens are advisory and these tests are
what stands behind it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.proven import is_parity_proven
from emporos.research.scans.raw_gap import (
    GapDirection,
    RawGapParameters,
    declared_arms,
    raw_gap_scan,
)

INSTRUMENT = "NSE:1"
FRIDAY, MONDAY, TUESDAY = date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)
EXECUTION = ScanExecution(Decimal(25_000))


def session(
    day: date, *, open_: str, close: str, bars: int = 75, skip_first: bool = False,
    partial_at: int | None = None,
) -> list[Candle]:  # fmt: skip
    """A session of 5m bars from 09:15 IST. The first bar opens at `open_`; every bar closes at
    `close`, with a rupee of range either side so orders can trade through."""
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)  # 09:15 IST
    out: list[Candle] = []
    for i in range(bars):
        if skip_first and i == 0:
            continue
        opening = Decimal(open_) if i == 0 else Decimal(close)
        closing = Decimal(close)
        high = max(opening, closing) + 1
        low = min(opening, closing) - 1
        out.append(
            Candle(
                INSTRUMENT,
                Timeframe.M5,
                start + timedelta(minutes=5 * i),
                Money.of(opening),
                Money.of(high),
                Money.of(low),
                Money.of(closing),
                1_000_000,
                partial=(i == partial_at),
            )  # fmt: skip
        )
    return out


def params(threshold: str = "1.0", direction: str = "continuation", entry_bar: int = 1):  # type: ignore[no-untyped-def]
    return RawGapParameters(Decimal(threshold), GapDirection(direction), entry_bar)


def trades(parameters: RawGapParameters, bars: list[Candle]):  # type: ignore[no-untyped-def]
    return raw_gap_scan(parameters, EXECUTION).scan(INSTRUMENT, bars)


def gap_up_two_days(open_: str = "102") -> list[Candle]:
    """Day 1 closes at 100; day 2 opens at `open_` and closes at 103."""
    return session(FRIDAY, open_="100", close="100") + session(MONDAY, open_=open_, close="103")


class TestDirection:
    def test_continuation_buys_a_gap_up_and_holds_to_the_close(self) -> None:
        (trade,) = trades(params(), gap_up_two_days())

        assert (trade.side, trade.day) == (OrderSide.BUY, MONDAY)
        assert trade.entry_price.amount == Decimal(103)  # the entry bar's close
        assert trade.exit_price.amount == Decimal(103)  # the 15:15 square-off bar's close

    def test_fade_sells_the_same_gap_up(self) -> None:
        (trade,) = trades(params(direction="fade"), gap_up_two_days())

        assert trade.side is OrderSide.SELL

    def test_a_gap_down_is_mirrored(self) -> None:
        bars = session(FRIDAY, open_="100", close="100") + session(MONDAY, open_="97", close="96")

        (cont,) = trades(params(), bars)
        (fade,) = trades(params(direction="fade"), bars)

        assert (cont.side, fade.side) == (OrderSide.SELL, OrderSide.BUY)


class TestThreshold:
    def test_a_gap_below_the_threshold_does_not_trade(self) -> None:
        assert trades(params("1.0"), gap_up_two_days(open_="100.99")) == []

    def test_a_gap_exactly_at_the_threshold_trades(self) -> None:
        assert len(trades(params("1.0"), gap_up_two_days(open_="101"))) == 1

    def test_a_bigger_threshold_needs_a_bigger_gap(self) -> None:
        assert trades(params("3.0"), gap_up_two_days(open_="102")) == []
        assert len(trades(params("3.0"), gap_up_two_days(open_="104"))) == 1


class TestEntryBar:
    def test_the_third_bar_enters_at_0930_at_that_bars_close(self) -> None:
        day2 = session(MONDAY, open_="102", close="103")
        day2[2] = Candle(
            INSTRUMENT, Timeframe.M5, day2[2].ts, Money.of(103), Money.of(105), Money.of(102),
            Money.of("104.5"), 1_000_000,
        )  # fmt: skip
        bars = session(FRIDAY, open_="100", close="100") + day2

        (trade,) = trades(params(entry_bar=3), bars)

        assert trade.entry_price.amount == Decimal("104.5")

    def test_the_entry_time_is_the_bars_start(self) -> None:
        assert params(entry_bar=1).entry_time.strftime("%H:%M") == "09:15"
        assert params(entry_bar=3).entry_time.strftime("%H:%M") == "09:25"


class TestDayRules:
    def test_the_first_day_has_no_previous_close_and_does_not_trade(self) -> None:
        assert trades(params(), session(MONDAY, open_="102", close="103")) == []

    def test_a_weekend_gap_counts_as_a_gap(self) -> None:
        (trade,) = trades(params(), gap_up_two_days())

        assert trade.day == MONDAY  # Friday's close to Monday's open

    def test_a_missing_opening_bar_skips_the_day(self) -> None:
        bars = session(FRIDAY, open_="100", close="100") + session(
            MONDAY, open_="102", close="103", skip_first=True
        )

        assert trades(params(), bars) == []

    def test_a_partial_entry_bar_does_not_trade(self) -> None:
        bars = session(FRIDAY, open_="100", close="100") + session(
            MONDAY, open_="102", close="103", partial_at=0
        )

        assert trades(params(), bars) == []

    def test_a_day_is_decided_once_at_the_entry_bar(self) -> None:
        """Below threshold at the entry bar: no later bar reopens the question."""
        bars = session(FRIDAY, open_="100", close="100") + session(
            MONDAY, open_="100.5", close="110"
        )

        assert trades(params(), bars) == []

    def test_each_day_trades_at_most_once(self) -> None:
        bars = (
            session(FRIDAY, open_="100", close="100")
            + session(MONDAY, open_="102", close="103")
            + session(TUESDAY, open_="106", close="107")
        )

        found = trades(params(), bars)

        assert [t.day for t in found] == [MONDAY, TUESDAY]

    def test_a_name_too_dear_for_the_declared_size_is_not_traded(self) -> None:
        bars = session(FRIDAY, open_="30000", close="30000") + session(
            MONDAY, open_="30600", close="30700"
        )

        assert trades(params(), bars) == []


class TestParameters:
    def test_a_threshold_and_entry_bar_must_be_sensible(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            RawGapParameters(Decimal(0), GapDirection.FADE, 1)
        with pytest.raises(ValueError, match="from 1"):
            RawGapParameters(Decimal(1), GapDirection.FADE, 0)

    def test_a_point_round_trips(self) -> None:
        point = params("1.5", "fade", 3).as_point()

        assert point == {"gap_threshold_pct": "1.5", "direction": "fade", "entry_bar": "3"}
        assert RawGapParameters.from_point(point) == params("1.5", "fade", 3)

    def test_the_scan_is_advisory_until_it_has_its_own_parity_case(self) -> None:
        assert raw_gap_scan(params(), EXECUTION).name == "raw_gap_hold_to_close"
        assert not is_parity_proven("raw_gap_hold_to_close")


class TestDeclaredArms:
    GRID: ClassVar[dict[str, tuple[str, ...]]] = {
        "gap_threshold_pct": ("1.0", "1.5"),
        "direction": ("continuation", "fade"),
        "entry_bar": ("1", "3"),
    }

    def test_every_combination_is_an_arm_in_a_fixed_order(self) -> None:
        arms = declared_arms(self.GRID)

        assert len(arms) == 8
        assert arms[0] == params("1.0", "continuation", 1) and arms[-1] == params("1.5", "fade", 3)

    def test_the_shipped_declaration_has_sixteen_arms(self) -> None:
        from emporos.cli.experiment_declarations import ExperimentDeclarationLoader

        declared = ExperimentDeclarationLoader().load(
            __import__("pathlib").Path("config/experiments/l3-raw-gap-hold-to-close.yaml")
        )

        assert len(declared_arms(declared.parameter_grid)) == 16
        assert declared.position_value == Decimal(25_000)

    def test_a_grid_that_names_other_parameters_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must name exactly"):
            declared_arms({**self.GRID, "stop": ("1",)})
        with pytest.raises(ValueError, match="must name exactly"):
            declared_arms({"gap_threshold_pct": ("1.0",)})

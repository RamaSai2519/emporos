"""EM-216, cell L17-daily-top-shock-fade: the candidate scan and the daily selection, pinned.

The scan is not parity-proven (no engine strategy), so its screens are advisory and these tests are
what stands behind it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.daily_selection import DailyTopKSelection
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.event_days import InstrumentSymbols
from emporos.research.scans.proven import is_parity_proven
from emporos.research.scans.top_shock import (
    RETRACE_MIN,
    EverySession,
    NewsFilter,
    ShockCandidate,
    TopShockParameters,
    TopShockScan,
    WithoutResultsReaction,
    declared_arms,
)
from emporos.research.screen_trades import ScreenTrade

FRIDAY, MONDAY, TUESDAY = date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)
EXECUTION = ScanExecution(Decimal(50_000))


def session(
    instrument: str, day: date, *, open_: str, at_hour: str, close: str, volume: int = 1_000_000
) -> list[Candle]:
    """5m bars from 09:15 IST: bar 1 opens at `open_`, bars 2..11 close there, bar 12 (10:10, the
    end of the first hour) closes at `at_hour`, bar 13 opens there, later bars close at `close`."""
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(75):
        closing = Decimal(open_ if i < 11 else at_hour if i == 11 else close)
        opening = Decimal(open_) if i == 0 else Decimal(at_hour) if i == 12 else closing
        out.append(
            Candle(
                instrument,
                Timeframe.M5,
                start + timedelta(minutes=5 * i),
                Money.of(opening),
                Money.of(max(opening, closing) + 1),
                Money.of(min(opening, closing) - 1),
                Money.of(closing),
                volume,
            )  # fmt: skip
        )
    return out


def gapped(instrument: str, open_: str, at_hour: str, close: str = "99", **kw: int) -> list[Candle]:
    """Friday closes 100; Monday opens at `open_`."""
    return session(instrument, FRIDAY, open_="100", at_hour="100", close="100") + session(
        instrument, MONDAY, open_=open_, at_hour=at_hour, close=close, **kw
    )


def arm(gap: str = "1.0", news: NewsFilter = NewsFilter.ALL) -> TopShockParameters:
    return TopShockParameters(Decimal(gap), news)


class TestParameters:
    def test_the_declared_grid_gives_four_arms(self) -> None:
        arms = declared_arms({"gap_floor_pct": ("1.0", "2.5"), "news": ("all", "no_results")})

        assert [a.as_point() for a in arms] == [
            {"gap_floor_pct": "1.0", "news": "all"},
            {"gap_floor_pct": "1.0", "news": "no_results"},
            {"gap_floor_pct": "2.5", "news": "all"},
            {"gap_floor_pct": "2.5", "news": "no_results"},
        ]

    def test_a_grid_naming_other_parameters_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"gap_floor_pct": ("1.0",), "retrace_min": ("0.5",)})

    def test_the_retrace_floor_is_the_parents_and_fixed(self) -> None:
        assert Decimal("0.25") == RETRACE_MIN
        assert arm("2.5").reversal.retrace_min == Decimal("0.25")
        assert arm("2.5").reversal.gap_threshold_pct == Decimal("2.5")

    def test_a_non_positive_floor_is_refused(self) -> None:
        with pytest.raises(ValueError):
            arm("0")


class TestCandidateScan:
    def test_a_confirmed_gap_up_is_faded_and_recorded_with_its_gap(self) -> None:
        scan = TopShockScan(arm(), EXECUTION, EverySession())

        (trade,) = scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101"))

        assert (trade.side, trade.day, trade.entry_price.amount) == (
            OrderSide.SELL,
            MONDAY,
            Decimal(101),
        )
        assert scan.candidates == (ShockCandidate("NSE:1", MONDAY, Decimal("0.02")),)

    def test_an_unconfirmed_gap_is_neither_traded_nor_recorded(self) -> None:
        scan = TopShockScan(arm(), EXECUTION, EverySession())

        assert scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101.9")) == []  # 0.05
        assert scan.candidates == ()

    def test_a_signal_is_recorded_even_when_its_order_cannot_fill(self) -> None:
        """Volume too thin for 10% participation: no fill, but the signal still competes."""
        scan = TopShockScan(arm(), EXECUTION, EverySession())

        assert scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101", volume=10)) == []
        assert [c.day for c in scan.candidates] == [MONDAY]

    def test_candidates_accumulate_across_instruments(self) -> None:
        scan = TopShockScan(arm(), EXECUTION, EverySession())
        scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101"))
        scan.scan("NSE:2", gapped("NSE:2", open_="97", at_hour="98.5"))

        assert {(c.instrument_id, c.score) for c in scan.candidates} == {
            ("NSE:1", Decimal("0.02")),
            ("NSE:2", Decimal("0.03")),
        }

    def test_the_floor_applies_before_recording(self) -> None:
        scan = TopShockScan(arm("2.5"), EXECUTION, EverySession())

        assert scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101")) == []
        assert scan.candidates == ()


class TestNoResults:
    def scan(self, filed: dict[str, list[datetime]]) -> TopShockScan:
        symbols = InstrumentSymbols({"NSE:1": "AAA", "NSE:2": "BBB"})
        no_results = arm(news=NewsFilter.NO_RESULTS)
        return TopShockScan(no_results, EXECUTION, WithoutResultsReaction(filed, symbols))

    def test_a_results_reaction_session_never_competes(self) -> None:
        # AAA filed on Sunday evening: Monday reacts
        filed = {"AAA": [datetime(2026, 1, 4, 18, 0, tzinfo=IST)]}
        scan = self.scan(filed)

        assert scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101")) == []
        assert scan.candidates == ()

    def test_another_names_filing_does_not_gate_this_one(self) -> None:
        filed = {"BBB": [datetime(2026, 1, 4, 18, 0, tzinfo=IST)]}
        scan = self.scan(filed)

        assert len(scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101"))) == 1

    def test_a_filing_during_the_session_leaves_the_day_open(self) -> None:
        filed = {"AAA": [datetime(2026, 1, 5, 11, 0, tzinfo=IST)]}  # mid-session: no reaction day
        scan = self.scan(filed)

        assert len(scan.scan("NSE:1", gapped("NSE:1", open_="102", at_hour="101"))) == 1


def trade(instrument: str, day: date) -> ScreenTrade:
    return ScreenTrade(instrument, day, OrderSide.SELL, Money.of(100), Money.of(99))


class TestDailySelection:
    def test_keeps_the_largest_score_per_day(self) -> None:
        signals = [
            ShockCandidate("NSE:1", MONDAY, Decimal("0.02")),
            ShockCandidate("NSE:2", MONDAY, Decimal("0.05")),
            ShockCandidate("NSE:3", TUESDAY, Decimal("0.01")),
        ]

        assert DailyTopKSelection(1).chosen(signals) == {("NSE:2", MONDAY), ("NSE:3", TUESDAY)}

    def test_k_two_keeps_two(self) -> None:
        signals = [ShockCandidate(f"NSE:{i}", MONDAY, Decimal(i)) for i in range(1, 5)]

        assert DailyTopKSelection(2).chosen(signals) == {("NSE:4", MONDAY), ("NSE:3", MONDAY)}

    def test_ties_go_to_the_lower_instrument_id(self) -> None:
        signals = [
            ShockCandidate("NSE:9", MONDAY, Decimal("0.03")),
            ShockCandidate("NSE:10", MONDAY, Decimal("0.03")),
        ]

        assert DailyTopKSelection(1).chosen(signals) == {("NSE:10", MONDAY)}

    def test_an_unfilled_choice_is_not_replaced_by_the_next_best(self) -> None:
        signals = [
            ShockCandidate("NSE:1", MONDAY, Decimal("0.05")),  # chosen, but it never filled
            ShockCandidate("NSE:2", MONDAY, Decimal("0.02")),
        ]
        filled = [trade("NSE:2", MONDAY)]

        assert DailyTopKSelection(1).keep(filled, signals) == []

    def test_keeps_only_chosen_trades_in_order(self) -> None:
        signals = [
            ShockCandidate("NSE:1", MONDAY, Decimal("0.05")),
            ShockCandidate("NSE:2", MONDAY, Decimal("0.02")),
            ShockCandidate("NSE:2", TUESDAY, Decimal("0.02")),
        ]
        trades = [trade("NSE:1", MONDAY), trade("NSE:2", MONDAY), trade("NSE:2", TUESDAY)]

        assert DailyTopKSelection(1).keep(trades, signals) == [trades[0], trades[2]]

    def test_k_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            DailyTopKSelection(0)


def test_the_scan_is_advisory() -> None:
    assert not is_parity_proven(TopShockScan.name)

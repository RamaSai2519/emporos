"""EM-227, cell L5-index-trend-day-leader: the index gate, the leader candidate, the two rank keys
and the volume baseline, pinned.

The scan is not parity-proven (no engine strategy), so its screens are advisory and these tests are
what stands behind it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.daily_selection import DailyTopKSelection
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.index_trend_leader import (
    BASELINE_MINIMUM,
    BASELINE_SESSIONS,
    IndexFirstHour,
    IndexTrendLeaderScan,
    LeaderCandidate,
    LeaderParameters,
    RankKey,
    declared_arms,
)
from emporos.research.scans.proven import is_parity_proven

FIRST = date(2026, 1, 1)
EXECUTION = ScanExecution(Decimal(50_000))
QUIET_HOUR_VOLUME = 1_000  # per bar, so a baseline first hour is 12,000


def day_after(n: int) -> date:
    return FIRST + timedelta(days=n)


def session(
    instrument: str,
    day: date,
    *,
    open_: str = "100",
    at_hour: str = "100",
    close: str = "100",
    first_hour_volume: int = QUIET_HOUR_VOLUME,
    later_volume: int = 1_000_000,
    skip_bar: int | None = None,
) -> list[Candle]:
    """5m bars from 09:15 IST. Bar 0 opens at `open_`, bar 11 (10:10, the end of the first hour)
    closes at `at_hour`, later bars close at `close`. `skip_bar` leaves that bar out of the first
    hour (a hole). First-hour bars carry `first_hour_volume`, later bars `later_volume`."""
    start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(75):
        if i == skip_bar:
            continue
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
                first_hour_volume if i <= 11 else later_volume,
            )  # fmt: skip
        )
    return out


def history(instrument: str, sessions: int = BASELINE_MINIMUM, **kw: int) -> list[Candle]:
    """`sessions` flat, quiet days before `day_after(sessions)`."""
    bars: list[Candle] = []
    for n in range(sessions):
        bars += session(instrument, day_after(n), **kw)  # type: ignore[arg-type]
    return bars


def index_series(moves: dict[int, str]) -> list[Candle]:
    """NIFTY sessions: `moves` maps a day offset to the 10:10 close (the open is 100)."""
    bars: list[Candle] = []
    for n, at_hour in moves.items():
        bars += session("NSE:99926000", day_after(n), at_hour=at_hour)
    return bars


def arm(theta: str = "0.4", key: RankKey = RankKey.VOLUME) -> LeaderParameters:
    return LeaderParameters(Decimal(theta), key)


def scan_for(
    index_moves: dict[int, str], parameters: LeaderParameters | None = None
) -> IndexTrendLeaderScan:
    index = IndexFirstHour.from_bars(index_series(index_moves))
    return IndexTrendLeaderScan(parameters or arm(), index, EXECUTION)


TODAY = BASELINE_MINIMUM  # the session after ten baseline sessions


class TestParameters:
    def test_the_declared_grid_gives_four_arms(self) -> None:
        arms = declared_arms({"index_move_min_pct": ("0.4", "0.5"), "rank_key": ("volume", "move")})

        assert [a.as_point() for a in arms] == [
            {"index_move_min_pct": "0.4", "rank_key": "volume"},
            {"index_move_min_pct": "0.4", "rank_key": "move"},
            {"index_move_min_pct": "0.5", "rank_key": "volume"},
            {"index_move_min_pct": "0.5", "rank_key": "move"},
        ]

    def test_a_grid_naming_other_parameters_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"index_move_min_pct": ("0.4",), "gap_floor_pct": ("1.0",)})

    def test_a_non_positive_floor_or_an_unknown_rank_key_is_refused(self) -> None:
        with pytest.raises(ValueError):
            arm("0")
        with pytest.raises(ValueError):
            LeaderParameters.from_point({"index_move_min_pct": "0.4", "rank_key": "size"})

    def test_the_baseline_window_is_the_declared_one(self) -> None:
        assert (BASELINE_SESSIONS, BASELINE_MINIMUM) == (20, 10)

    def test_the_scan_is_advisory(self) -> None:
        assert not is_parity_proven(IndexTrendLeaderScan.name)


class TestIndexFirstHour:
    def test_the_move_is_signed_from_the_open_to_the_1010_close(self) -> None:
        index = IndexFirstHour.from_bars(index_series({0: "101", 1: "99.5"}))

        assert index.move(day_after(0)) == Decimal("0.01")
        assert index.move(day_after(1)) == Decimal("-0.005")
        assert index.move(day_after(2)) is None

    def test_a_session_without_its_1010_bar_has_no_move(self) -> None:
        # 10:10 IST is 04:40 UTC
        no_decision = [b for b in index_series({0: "101"}) if (b.ts.hour, b.ts.minute) != (4, 40)]

        assert IndexFirstHour.from_bars(no_decision).move(day_after(0)) is None


class TestLeaderCandidate:
    def test_a_leader_on_a_gated_up_day_is_bought_and_recorded_by_participation(self) -> None:
        scan = scan_for({TODAY: "100.5"})  # NIFTY +0.5%
        bars = history("NSE:1") + session(
            "NSE:1", day_after(TODAY), at_hour="101", first_hour_volume=3 * QUIET_HOUR_VOLUME
        )

        (trade,) = scan.scan("NSE:1", bars)

        assert (trade.side, trade.day, trade.entry_price.amount) == (
            OrderSide.BUY,
            day_after(TODAY),
            Decimal(101),
        )
        # today's first hour is 12 x 3,000 = 36,000 against a 12,000 baseline mean
        assert scan.candidates == (LeaderCandidate("NSE:1", day_after(TODAY), Decimal(3)),)

    def test_the_move_key_scores_the_names_own_same_direction_move(self) -> None:
        scan = scan_for({TODAY: "100.5"}, arm(key=RankKey.MOVE))
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="101.5")

        scan.scan("NSE:1", bars)

        assert scan.candidates == (LeaderCandidate("NSE:1", day_after(TODAY), Decimal("0.015")),)

    def test_a_down_day_is_a_short_scored_by_the_absolute_move(self) -> None:
        scan = scan_for({TODAY: "99.4"}, arm(key=RankKey.MOVE))
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="98.5")

        (trade,) = scan.scan("NSE:1", bars)

        assert trade.side is OrderSide.SELL
        assert scan.candidates[0].score == Decimal("0.015")

    def test_an_index_move_under_the_floor_opens_no_gate(self) -> None:
        scan = scan_for({TODAY: "100.3"})  # +0.3% against a 0.4% floor
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="101")

        assert scan.scan("NSE:1", bars) == []
        assert scan.candidates == ()

    def test_the_floor_is_inclusive(self) -> None:
        scan = scan_for({TODAY: "100.4"})
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="101")

        assert len(scan.scan("NSE:1", bars)) == 1

    def test_a_name_moving_against_the_index_is_not_a_candidate(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="99")

        assert scan.scan("NSE:1", bars) == []
        assert scan.candidates == ()

    def test_a_name_lagging_the_index_is_not_a_candidate(self) -> None:
        scan = scan_for({TODAY: "100.5"})  # NIFTY +0.5%, the name only +0.4%
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="100.4")

        assert scan.candidates == ()
        assert scan.scan("NSE:1", bars) == []

    def test_a_name_exactly_matching_the_index_is_not_leading_it(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="100.5")

        assert scan.scan("NSE:1", bars) == []

    def test_a_session_the_index_has_no_move_for_is_not_traded(self) -> None:
        scan = scan_for({TODAY + 5: "101"})
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="101")

        assert scan.scan("NSE:1", bars) == []


class TestVolumeBaseline:
    def test_fewer_than_ten_baseline_sessions_gives_no_candidate(self) -> None:
        scan = scan_for({BASELINE_MINIMUM - 1: "100.5"})
        bars = history("NSE:1", BASELINE_MINIMUM - 1) + session(
            "NSE:1", day_after(BASELINE_MINIMUM - 1), at_hour="101"
        )

        assert scan.scan("NSE:1", bars) == []
        assert scan.candidates == ()

    def test_sessions_with_a_hole_in_the_first_hour_do_not_count(self) -> None:
        holed: list[Candle] = []
        for n in range(BASELINE_MINIMUM):
            holed += session("NSE:1", day_after(n), skip_bar=4 if n < 3 else None)
        scan = scan_for({TODAY: "100.5"})

        scan.scan("NSE:1", holed + session("NSE:1", day_after(TODAY), at_hour="101"))

        assert scan.candidates == ()  # 7 whole first hours, 3 holed: under the minimum of 10

    def test_ten_of_the_previous_twenty_is_enough_when_others_are_holed(self) -> None:
        bars: list[Candle] = []
        for n in range(BASELINE_SESSIONS):
            bars += session("NSE:1", day_after(n), skip_bar=4 if n % 2 else None)  # 10 whole
        today = BASELINE_SESSIONS
        scan = scan_for({today: "100.5"})

        scan.scan("NSE:1", bars + session("NSE:1", day_after(today), at_hour="101"))

        assert scan.candidates == (LeaderCandidate("NSE:1", day_after(today), Decimal(1)),)

    def test_only_the_previous_twenty_sessions_form_the_baseline(self) -> None:
        old = history("NSE:1", 5, first_hour_volume=100 * QUIET_HOUR_VOLUME)  # far back, huge
        recent: list[Candle] = []
        for n in range(5, 5 + BASELINE_SESSIONS):
            recent += session("NSE:1", day_after(n))
        today = 5 + BASELINE_SESSIONS
        scan = scan_for({today: "100.5"})

        scan.scan("NSE:1", old + recent + session("NSE:1", day_after(today), at_hour="101"))

        assert scan.candidates[0].score == Decimal(1)  # the huge sessions fell out of the window

    def test_a_holed_first_hour_today_gives_no_candidate(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="101", skip_bar=3)

        assert scan.scan("NSE:1", bars) == []

    def test_today_is_never_in_its_own_baseline(self) -> None:
        """A first-hour spike today must not lift the mean it is divided by."""
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1") + session(
            "NSE:1", day_after(TODAY), at_hour="101", first_hour_volume=10 * QUIET_HOUR_VOLUME
        )

        scan.scan("NSE:1", bars)

        assert scan.candidates[0].score == Decimal(10)

    def test_the_move_key_needs_the_baseline_too_so_both_keys_see_the_same_candidates(self) -> None:
        scan = scan_for({BASELINE_MINIMUM - 1: "100.5"}, arm(key=RankKey.MOVE))
        bars = history("NSE:1", BASELINE_MINIMUM - 1) + session(
            "NSE:1", day_after(BASELINE_MINIMUM - 1), at_hour="101.5"
        )

        assert scan.scan("NSE:1", bars) == []
        assert scan.candidates == ()

    def test_a_baseline_of_zero_volume_gives_no_candidate_instead_of_dividing_by_it(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1", first_hour_volume=0) + session(
            "NSE:1", day_after(TODAY), at_hour="101"
        )

        assert scan.scan("NSE:1", bars) == []


class TestSignalsBeforeFillsAndNoLookAhead:
    def test_a_signal_is_recorded_even_when_its_order_cannot_fill(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1") + session(
            "NSE:1",
            day_after(TODAY),
            at_hour="101",
            later_volume=1,  # 10% of 1 share: no fill
        )

        assert scan.scan("NSE:1", bars) == []
        assert len(scan.candidates) == 1

    def test_nothing_after_the_1010_bar_changes_a_candidate(self) -> None:
        def candidates(close: str, later_volume: int) -> tuple[LeaderCandidate, ...]:
            scan = scan_for({TODAY: "100.5"})
            scan.scan(
                "NSE:1",
                history("NSE:1")
                + session(
                    "NSE:1", day_after(TODAY), at_hour="101", close=close, later_volume=later_volume
                ),
            )
            return scan.candidates

        assert candidates("95", 1_000_000) == candidates("140", 5_000_000)

    def test_a_name_too_dear_for_the_position_is_not_a_candidate(self) -> None:
        scan = scan_for({TODAY: "100.5"})
        bars = history("NSE:1", open_="60000", at_hour="60000", close="60000") + session(
            "NSE:1", day_after(TODAY), open_="60000", at_hour="60600", close="60600"
        )

        assert scan.scan("NSE:1", bars) == []
        assert scan.candidates == ()


class TestRankKeysChooseDifferentNames:
    def test_volume_and_move_keys_pick_different_leaders_from_the_same_candidates(self) -> None:
        """NSE:1 leads on extension, NSE:2 on participation: the arms are different tests."""
        index_moves = {TODAY: "100.5"}
        picks: dict[RankKey, str] = {}
        for key in RankKey:
            scan = scan_for(index_moves, arm(key=key))
            trades = scan.scan(
                "NSE:1", history("NSE:1") + session("NSE:1", day_after(TODAY), at_hour="103")
            ) + scan.scan(
                "NSE:2",
                history("NSE:2")
                + session(
                    "NSE:2",
                    day_after(TODAY),
                    at_hour="101",
                    first_hour_volume=5 * QUIET_HOUR_VOLUME,
                ),
            )
            (chosen,) = DailyTopKSelection(1).chosen(scan.candidates)
            picks[key] = chosen[0]
            assert len(trades) == 2

        assert picks == {RankKey.VOLUME: "NSE:2", RankKey.MOVE: "NSE:1"}

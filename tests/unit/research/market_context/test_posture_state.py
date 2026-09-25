"""EM-239: the daily posture's numbers; nothing after 09:00 IST reaches them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from tests.unit.research.market_context.test_context import Loader, day_bars

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.eventtrader.events import MarketEvent
from emporos.research.market_context.bars import BarSeriesCache, IntradayBars
from emporos.research.market_context.breadth import Breadth, SessionCloseTable
from emporos.research.market_context.global_cues import CueSeries, GlobalCues
from emporos.research.market_context.posture_state import (
    BreadthSection,
    FilingCounts,
    GlobalCuesSection,
    IndexState,
    NumericPostureInputs,
    PostureSection,
)

NIFTY, VIX = "NSE:99926000", "NSE:99926017"
SESSIONS = [
    d for d in (date(2026, 1, 5) + timedelta(days=i) for i in range(120)) if d.weekday() < 5
][:70]
TARGET = SESSIONS[65]  # the morning under test
PREVIOUS = SESSIONS[64]


def flat_series(
    instrument: str, closes: Sequence[float], sessions: Sequence[date] = SESSIONS
) -> list[Candle]:
    """One flat session per day: every bar of session n is closes[n]."""
    out: list[Candle] = []
    for day, close in zip(sessions, closes, strict=False):
        out += day_bars(instrument, day, [close] * 75)
    return out


def nifty_closes() -> list[float]:
    return [100.0 + n for n in range(len(SESSIONS))]


def vix_closes() -> list[float]:
    return [15.0 + (n % 10) for n in range(len(SESSIONS))]


def world() -> dict[str, list[Candle]]:
    return {NIFTY: flat_series(NIFTY, nifty_closes()), VIX: flat_series(VIX, vix_closes())}


def cache(data: dict[str, list[Candle]]) -> BarSeriesCache:
    return BarSeriesCache(Loader(data), SESSIONS[0], SESSIONS[-1])


def at9(day: date) -> datetime:
    return datetime.combine(day, time(9, 0), tzinfo=IST)


class TestClosesBefore:
    def test_it_returns_only_completed_sessions_before_the_day(self) -> None:
        bars = IntradayBars(flat_series(NIFTY, nifty_closes()))
        got = bars.closes_before(TARGET, 3, at9(TARGET))
        assert got == [(SESSIONS[62], 162.0), (SESSIONS[63], 163.0), (SESSIONS[64], 164.0)]

    def test_a_session_still_running_at_the_decision_time_shows_its_completed_bars(self) -> None:
        bars = IntradayBars(flat_series(NIFTY, nifty_closes()))
        noon = datetime.combine(SESSIONS[64], time(12, 0), tzinfo=IST)
        assert bars.closes_before(SESSIONS[65], 1, noon)[-1][0] == SESSIONS[64]


class TestIndexState:
    def lines(self, data: dict[str, list[Candle]] | None = None) -> dict[str, float]:
        state = IndexState(cache(data or world()), NIFTY, VIX)
        return state.lines(TARGET, PREVIOUS, at9(TARGET))

    def test_nifty_numbers(self) -> None:
        got = self.lines()
        assert got["nifty_prev_close"] == 164.0
        assert got["nifty_prev_day_move_pct"] == pytest.approx((164 / 163 - 1) * 100)
        assert got["nifty_ret_5d_pct"] == pytest.approx((164 / 159 - 1) * 100)
        assert got["nifty_ret_20d_pct"] == pytest.approx((164 / 144 - 1) * 100)

    def test_vix_level_change_and_percentile(self) -> None:
        got = self.lines()
        closes = vix_closes()[:65]  # sessions before the target
        assert got["india_vix"] == closes[-1]
        assert got["india_vix_change_pct"] == pytest.approx((closes[-1] / closes[-2] - 1) * 100)
        assert got["india_vix_percentile"] == pytest.approx(
            100 * sum(v <= closes[-1] for v in closes) / len(closes)
        )
        assert got["india_vix_percentile_sessions"] == 65.0

    def test_a_short_vix_history_gives_no_percentile(self) -> None:
        data = {
            NIFTY: flat_series(NIFTY, nifty_closes()),
            VIX: flat_series(VIX, vix_closes()[:40], SESSIONS[:40]),
        }
        state = IndexState(cache(data), NIFTY, VIX)
        got = state.lines(SESSIONS[40], SESSIONS[39], at9(SESSIONS[40]))
        assert "india_vix_percentile" not in got and got["india_vix"] == vix_closes()[39]

    def test_a_series_that_stops_before_the_previous_session_says_nothing(self) -> None:
        data = {NIFTY: flat_series(NIFTY, nifty_closes()[:60], SESSIONS[:60]), VIX: world()[VIX]}
        got = self.lines(data)
        assert not any(k.startswith("nifty_") for k in got) and "india_vix" in got


class TestBreadth:
    def table(self, names: int, up: int) -> SessionCloseTable:
        closes = {}
        for n in range(names):
            rising = n < up
            closes[f"NSE:{n}"] = [
                (d, 100.0 + (i if rising or i < 64 else -1)) for i, d in enumerate(SESSIONS[:65])
            ]
        return SessionCloseTable(closes)

    def test_shares_of_names_up_and_above_their_average(self) -> None:
        got = Breadth(self.table(40, 30)).lines(TARGET, PREVIOUS)
        assert got["breadth_names"] == 40.0
        assert got["breadth_up_share"] == pytest.approx(0.75)
        assert got["breadth_above_20d_share"] == pytest.approx(0.75)

    def test_too_few_names_is_no_breadth(self) -> None:
        assert Breadth(self.table(10, 5)).lines(TARGET, PREVIOUS) == {}

    def test_the_decision_day_and_later_closes_are_unreachable(self) -> None:
        clean = self.table(40, 30)
        dirty = SessionCloseTable(
            {
                i: [*clean.history_before(i, TARGET, 100), (TARGET, 1e9), (SESSIONS[66], -5.0)]
                for i in clean.instrument_ids
            }
        )
        assert Breadth(dirty).lines(TARGET, PREVIOUS) == Breadth(clean).lines(TARGET, PREVIOUS)

    def test_the_table_takes_the_last_bar_of_each_session(self) -> None:
        loader = Loader({"NSE:1": day_bars("NSE:1", SESSIONS[0], [10.0, 11.0, 12.0])})
        table = SessionCloseTable.load(loader, ["NSE:1"], SESSIONS[0], SESSIONS[-1])
        assert table.history_before("NSE:1", SESSIONS[1], 5) == [(SESSIONS[0], 12.0)]


def filing(
    seq: int, category: str, published: datetime, usable: datetime | None = None
) -> MarketEvent:
    return MarketEvent(
        f"NSE:{seq}", "NSE:1", "ABB", published, usable or published, "filing", category, "s", "t"
    )


class FakeStore:
    def __init__(self, events: list[MarketEvent]) -> None:
        self._events = events

    def events_between(self, start: datetime, end: datetime) -> list[MarketEvent]:
        return [e for e in self._events if start <= e.published_at < end]

    def for_symbol(self, symbol: str, start: datetime, end: datetime) -> list[MarketEvent]:
        return [e for e in self.events_between(start, end) if e.symbol == symbol]

    def by_id(self, event_id: str) -> MarketEvent | None:
        return next((e for e in self._events if e.event_id == event_id), None)


class TestFilingCounts:
    def test_only_material_filings_between_the_previous_close_and_nine_count(self) -> None:
        close = datetime.combine(PREVIOUS, time(15, 30), tzinfo=IST)
        nine = at9(TARGET)
        events = [
            filing(1, "Outcome of Board Meeting", close - timedelta(seconds=1)),  # before the close
            filing(2, "Outcome of Board Meeting", close),
            filing(3, "Outcome of Board Meeting", nine - timedelta(hours=3)),
            filing(4, "Acquisition", nine - timedelta(minutes=1)),
            filing(5, "Loss of Share Certificates", nine - timedelta(hours=1)),  # routine
            filing(6, "Acquisition", nine),  # at 09:00 sharp: after the read
            filing(7, "Acquisition", nine + timedelta(hours=2)),
            filing(8, "Acquisition", nine - timedelta(hours=2), usable=nine + timedelta(hours=1)),
        ]
        got = FilingCounts(FakeStore(events)).lines(TARGET, PREVIOUS, nine)
        assert got == {
            "material_filings_prev_day": 3.0,
            "material_filings_acquisition": 1.0,
            "material_filings_outcome_of_board_meeting": 2.0,
        }

    def test_a_quiet_night_is_a_zero_not_a_missing_key(self) -> None:
        assert FilingCounts(FakeStore([])).lines(TARGET, PREVIOUS, at9(TARGET)) == {
            "material_filings_prev_day": 0.0
        }


def sections(data: dict[str, list[Candle]]) -> tuple[BarSeriesCache, list[PostureSection]]:
    series = cache(data)
    cues = GlobalCues(
        {"sp500": CueSeries([(SESSIONS[63], 100.0), (PREVIOUS, 101.0), (TARGET, 1e9)])}
    )
    return series, [
        IndexState(series, NIFTY, VIX),
        GlobalCuesSection(cues),
        FilingCounts(FakeStore([])),
    ]


class TestNumericPostureInputs:
    def test_it_is_numbers_at_nine_ist_with_no_headlines(self) -> None:
        series, parts = sections(world())
        item = NumericPostureInputs(parts, series, NIFTY).for_day(TARGET)
        assert item.headlines == []
        assert item.decision_at == datetime.combine(TARGET, time(9, 0), tzinfo=IST)
        assert {"nifty_prev_close", "india_vix", "material_filings_prev_day"} <= set(item.state)
        assert item.state["sp500_prev_close_pct"] == pytest.approx(1.0)

    def test_a_day_with_no_previous_session_has_an_empty_state(self) -> None:
        series, parts = sections(world())
        assert NumericPostureInputs(parts, series, NIFTY).for_day(SESSIONS[0]).state == {}

    def test_later_data_with_absurd_values_changes_nothing(self) -> None:
        """Every bar from 09:00 IST on the decision day, and every later day, is replaced by an
        absurd one; the state must be identical."""
        clean = world()
        cut = at9(TARGET)

        def poisoned(instrument: str) -> list[Candle]:
            return [
                Candle(
                    b.instrument_id,
                    b.timeframe,
                    b.ts,
                    Money.of("9999999"),
                    Money.of("9999999"),
                    Money.of("1"),
                    Money.of("9999999"),
                    10**9,
                )  # fmt: skip
                if b.ts >= cut.astimezone(UTC)
                else b
                for b in clean[instrument]
            ]

        dirty = {NIFTY: poisoned(NIFTY), VIX: poisoned(VIX)}
        assert any(b.close.amount == 9999999 for b in dirty[NIFTY])
        a_series, a_parts = sections(clean)
        b_series, b_parts = sections(dirty)
        a = NumericPostureInputs(a_parts, a_series, NIFTY).for_day(TARGET)
        b = NumericPostureInputs(b_parts, b_series, NIFTY).for_day(TARGET)
        assert a.state == b.state and a.state["nifty_prev_close"] == 164.0

    def test_breadth_section_delegates(self) -> None:
        table = SessionCloseTable(
            {f"NSE:{n}": [(d, 100.0 + i) for i, d in enumerate(SESSIONS[:65])] for n in range(35)}
        )
        got = BreadthSection(Breadth(table)).lines(TARGET, PREVIOUS, at9(TARGET))
        assert got["breadth_up_share"] == 1.0


class TestSavedTable:
    def test_a_saved_table_is_reopened_only_for_the_same_window_and_names(
        self, tmp_path: Path
    ) -> None:
        table = SessionCloseTable({"NSE:1": [(SESSIONS[0], 10.0), (SESSIONS[1], 11.0)]})
        target = tmp_path / "closes.json"
        table.save(target, SESSIONS[0], SESSIONS[-1])
        same = SessionCloseTable.open(target, SESSIONS[0], SESSIONS[-1], ["NSE:1"])
        assert same is not None
        assert same.history_before("NSE:1", SESSIONS[2], 5) == [
            (SESSIONS[0], 10.0),
            (SESSIONS[1], 11.0),
        ]
        assert SessionCloseTable.open(target, SESSIONS[0], SESSIONS[-2], ["NSE:1"]) is None
        assert SessionCloseTable.open(target, SESSIONS[0], SESSIONS[-1], ["NSE:1", "NSE:2"]) is None
        assert SessionCloseTable.open(tmp_path / "none.json", SESSIONS[0], SESSIONS[-1], []) is None

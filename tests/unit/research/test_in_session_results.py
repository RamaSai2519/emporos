"""EM-231, cell L2-in-session-results-drift: the anchor, the reaction bar and the release buffer,
pinned.

The scan is not parity-proven (no engine strategy), so its screens are advisory and these tests are
what stands behind it. The one that matters most: no bar that started before the release can ever
be the reaction bar."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import EntryIntent, ScanExecution
from emporos.research.scans.event_days import InstrumentSymbols
from emporos.research.scans.in_session_results import (
    BUFFER,
    MAX_LATE,
    Direction,
    InSessionOutcome,
    InSessionParameters,
    InSessionResultsRules,
    InSessionResultsScan,
    declared_arms,
    in_window,
)

MONDAY = date(2026, 1, 5)
EXECUTION = ScanExecution(Decimal(50_000))
CUTOFF = EXECUTION.no_new_entries_after
SYMBOL, INSTRUMENT = "ABC", "NSE:1"


def at(hour: int, minute: int, day: date = MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def session(
    day: date = MONDAY, closes: dict[str, str] | None = None, *, missing: tuple[str, ...] = ()
) -> list[Candle]:
    """5m bars from 09:15 to 15:25 IST, flat at 100 except where `closes` names a bar's start time
    ("10:10") and its close. A bar named in `missing` is left out (a hole)."""
    closes = closes or {}
    out: list[Candle] = []
    previous = Decimal(100)
    for i in range(75):
        start = at(9, 15, day) + timedelta(minutes=5 * i)
        label = start.strftime("%H:%M")
        close = Decimal(closes.get(label, "100"))
        if label not in missing:
            out.append(
                Candle(
                    INSTRUMENT,
                    Timeframe.M5,
                    start.astimezone(UTC),
                    Money.of(previous),
                    Money.of(max(previous, close) + 1),
                    Money.of(min(previous, close) - 1),
                    Money.of(close),
                    1_000_000,
                )
            )
        previous = close
    return out


def params(
    theta: str = "1.5", direction: Direction = Direction.CONTINUATION
) -> InSessionParameters:
    return InSessionParameters(Decimal(theta), direction)


def rules(
    published: datetime, parameters: InSessionParameters | None = None
) -> tuple[InSessionResultsRules, InSessionOutcome]:
    outcome = InSessionOutcome()
    return (
        InSessionResultsRules(parameters or params(), {MONDAY: [published]}, CUTOFF, outcome),
        outcome,
    )


def intents(rule: InSessionResultsRules, bars: list[Candle]) -> dict[str, EntryIntent | None]:
    out: dict[str, EntryIntent | None] = {}
    for bar in bars:
        intent = rule.observe(bar, live=True, held=None, can_afford=True)
        out[bar.ts.astimezone(IST).strftime("%H:%M")] = intent  # type: ignore[assignment]
    return out


def scan_of(
    events: list[datetime], parameters: InSessionParameters | None = None
) -> InSessionResultsScan:
    return InSessionResultsScan(
        parameters or params(),
        EXECUTION,
        {SYMBOL: events},
        InstrumentSymbols({INSTRUMENT: SYMBOL}),
    )


class TestTheReactionBar:
    def test_a_bar_that_started_before_the_release_is_never_the_reaction_bar(self) -> None:
        # released 10:02; the 10:00 and 10:05 bars started before the release + buffer and move 5%
        rule, outcome = rules(at(10, 2))
        bars = session(closes={"10:00": "105", "10:05": "105", "10:10": "100"})

        result = intents(rule, bars)

        assert all(v is None for v in result.values())
        assert outcome.measured_by_year[2026] == 1  # the 10:10 bar was the reaction: flat

    def test_the_first_bar_at_or_after_the_buffer_is_the_reaction_bar(self) -> None:
        rule, _ = rules(at(10, 0))  # buffer ends 10:05: that bar starts exactly then

        result = intents(rule, session(closes={"10:05": "103"}))

        assert result["10:05"] == EntryIntent(OrderSide.BUY)

    def test_a_release_between_bars_waits_for_the_next_whole_buffer(self) -> None:
        rule, _ = rules(at(10, 2))  # 10:02 + 5 = 10:07: the 10:05 bar started too early

        result = intents(rule, session(closes={"10:05": "103", "10:10": "103"}))

        assert result["10:05"] is None
        assert result["10:10"] == EntryIntent(OrderSide.BUY)

    def test_the_buffer_and_lateness_are_the_declared_ones(self) -> None:
        assert (timedelta(minutes=5), timedelta(minutes=10)) == (BUFFER, MAX_LATE)

    def test_a_hole_in_the_bars_after_the_buffer_is_counted_and_not_traded(self) -> None:
        rule, outcome = rules(at(10, 2))
        holes = ("10:10", "10:15", "10:20")  # next bar 10:25 starts after 10:02 + 5 + 10 = 10:17

        result = intents(rule, session(closes={"10:25": "110"}, missing=holes))

        assert all(v is None for v in result.values())
        assert outcome.no_reaction_bar == 1
        assert sum(outcome.measured_by_year.values()) == 0

    def test_a_reaction_bar_just_inside_the_lateness_is_used(self) -> None:
        rule, outcome = rules(at(10, 2))  # 10:17 is the latest start; the 10:15 bar starts before
        holes = ("10:10",)

        result = intents(rule, session(closes={"10:15": "103"}, missing=holes))

        assert result["10:15"] == EntryIntent(OrderSide.BUY)
        assert outcome.no_reaction_bar == 0


class TestTheAnchor:
    def test_it_is_the_close_of_the_last_bar_that_ended_at_or_before_the_release(self) -> None:
        # released 10:03: the 10:00 bar ends 10:05, after it, so the 09:55 bar is the anchor
        rule, _ = rules(at(10, 3))
        bars = session(closes={"09:55": "100", "10:00": "90", "10:10": "101.4"})

        assert intents(rule, bars)["10:10"] is None  # 1.4% against an anchor of 100, not 90

    def test_a_release_on_a_bar_boundary_uses_the_bar_that_just_ended(self) -> None:
        rule, _ = rules(at(10, 0))
        bars = session(closes={"09:55": "100", "10:05": "102"})

        assert intents(rule, bars)["10:05"] == EntryIntent(OrderSide.BUY)

    def test_a_release_in_the_first_minutes_has_no_anchor(self) -> None:
        rule, outcome = rules(at(9, 16))  # the 09:15 bar has not ended

        assert all(v is None for v in intents(rule, session(closes={"09:25": "110"})).values())
        assert outcome.no_anchor == 1

    def test_a_previous_days_bar_is_never_an_anchor(self) -> None:
        rule, outcome = rules(at(9, 16))
        yesterday = session(MONDAY - timedelta(days=1), closes={"15:25": "200"})

        assert all(v is None for v in intents(rule, yesterday + session()).values())
        assert outcome.no_anchor == 1


class TestTheSignal:
    @pytest.mark.parametrize(("close", "fires"), [("101.4", False), ("101.5", True)])
    def test_a_move_of_at_least_theta_fires(self, close: str, fires: bool) -> None:
        rule, _ = rules(at(10, 0))

        result = intents(rule, session(closes={"10:05": close}))

        assert (result["10:05"] is not None) is fires

    def test_theta_3_needs_a_bigger_move(self) -> None:
        rule, _ = rules(at(10, 0), params("3.0"))

        assert intents(rule, session(closes={"10:05": "102"}))["10:05"] is None

    @pytest.mark.parametrize(
        ("direction", "close", "side"),
        [
            (Direction.CONTINUATION, "103", OrderSide.BUY),
            (Direction.CONTINUATION, "97", OrderSide.SELL),
            (Direction.FADE, "103", OrderSide.SELL),
            (Direction.FADE, "97", OrderSide.BUY),
        ],
    )
    def test_the_direction_arm_sets_the_side(
        self, direction: Direction, close: str, side: OrderSide
    ) -> None:
        rule, _ = rules(at(10, 0), params(direction=direction))

        assert intents(rule, session(closes={"10:05": close}))["10:05"] == EntryIntent(side)

    def test_the_long_and_short_signals_are_counted_separately(self) -> None:
        rule, outcome = rules(at(10, 0))
        intents(rule, session(closes={"10:05": "97"}))

        assert (outcome.long_signals, outcome.short_signals) == (0, 1)

    def test_an_event_is_resolved_once_only(self) -> None:
        rule, outcome = rules(at(10, 0))
        intents(rule, session(closes={"10:05": "103", "10:10": "103"}))

        assert outcome.measured_by_year[2026] == 1
        assert outcome.long_signals == 1

    def test_no_entry_when_held_not_live_or_unaffordable(self) -> None:
        for kwargs in (
            {"held": OrderSide.BUY, "live": True, "can_afford": True},
            {"held": None, "live": False, "can_afford": True},
            {"held": None, "live": True, "can_afford": False},
        ):
            rule, _ = rules(at(10, 0))
            bars = session(closes={"10:05": "103"})
            got = [rule.observe(b, **kwargs) for b in bars]  # type: ignore[arg-type]
            assert all(v is None for v in got)

    def test_no_entry_after_the_cutoff(self) -> None:
        rule, _ = rules(at(14, 30))  # reaction 14:35 closes 14:40, before 14:45: allowed
        assert intents(rule, session(closes={"14:35": "103"}))["14:35"] == EntryIntent(
            OrderSide.BUY
        )
        # a reaction bar closing at or after the cutoff never enters
        cut, _ = rules(at(14, 38))
        assert all(v is None for v in intents(cut, session(closes={"14:45": "110"})).values())


class TestTheWindow:
    @pytest.mark.parametrize(
        ("moment", "inside"),
        [
            (at(9, 14), False),
            (at(9, 15), True),
            (at(14, 30), True),
            (at(14, 31), False),
            (at(16, 0), False),
            (at(11, 0, date(2026, 1, 3)), False),  # a Saturday
            (at(11, 0, date(2026, 1, 4)), False),  # a Sunday
        ],
    )
    def test_in_window(self, moment: datetime, inside: bool) -> None:
        assert in_window(moment) is inside

    def test_the_window_reads_ist_not_utc(self) -> None:
        assert in_window(at(10, 0).astimezone(UTC))


class TestTheScan:
    def test_a_continuation_trade_is_taken_after_the_reaction_bar(self) -> None:
        bars = session(closes={"10:05": "103", "10:10": "103", "10:15": "103"})

        (trade,) = scan_of([at(10, 0)]).scan(INSTRUMENT, bars)

        assert (trade.side, trade.day) == (OrderSide.BUY, MONDAY)

    def test_the_fade_arm_trades_against(self) -> None:
        bars = session(closes={"10:05": "103"})

        trades = scan_of([at(10, 0)], params(direction=Direction.FADE)).scan(INSTRUMENT, bars)

        assert [t.side for t in trades] == [OrderSide.SELL]

    def test_events_outside_the_window_are_counted_not_traded(self) -> None:
        scan = scan_of([at(8, 30), at(15, 0), at(11, 0, date(2026, 1, 3))])

        assert scan.scan(INSTRUMENT, session()) == []
        assert scan.outcome.outside_window == 2  # the Saturday is out of the bars' range

    def test_a_day_in_range_with_no_bars_is_counted(self) -> None:
        wednesday = date(2026, 1, 7)
        bars = session() + session(date(2026, 1, 8))
        scan = scan_of([at(10, 0, wednesday)])

        assert scan.scan(INSTRUMENT, bars) == []
        assert scan.outcome.no_session_bars == 1

    def test_events_are_measured_per_year(self) -> None:
        scan = scan_of([at(10, 0)])
        scan.scan(INSTRUMENT, session())

        assert dict(scan.outcome.measured_by_year) == {2026: 1}

    def test_other_symbols_events_are_ignored(self) -> None:
        scan = InSessionResultsScan(
            params(),
            EXECUTION,
            {"OTHER": [at(10, 0)]},
            InstrumentSymbols({INSTRUMENT: SYMBOL}),
        )

        assert scan.scan(INSTRUMENT, session(closes={"10:05": "110"})) == []

    def test_no_bars_no_trades(self) -> None:
        assert scan_of([at(10, 0)]).scan(INSTRUMENT, []) == []


class TestTheDeclaration:
    def test_the_grid_gives_four_arms(self) -> None:
        arms = declared_arms({"theta_pct": ("1.5", "3.0"), "direction": ("continuation", "fade")})

        assert [a.as_point() for a in arms] == [
            {"theta_pct": "1.5", "direction": "continuation"},
            {"theta_pct": "1.5", "direction": "fade"},
            {"theta_pct": "3.0", "direction": "continuation"},
            {"theta_pct": "3.0", "direction": "fade"},
        ]

    def test_a_grid_with_other_axes_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"theta_pct": ("1.5",), "buffer": ("5",)})

    def test_a_non_positive_theta_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            InSessionParameters(Decimal(0), Direction.FADE)

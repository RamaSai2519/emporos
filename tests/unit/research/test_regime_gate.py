"""EM-191 L4: the regime day gate and the VIX-gated gap reversal built on it."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from tests.unit.research.test_shock_reversal import FRIDAY, MONDAY, gap_up, session

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import EntryIntent, ScanExecution, ScanIntent
from emporos.research.scans.regime_gate import (
    DayGatedRules,
    TrailingPercentileDays,
    session_openings,
)
from emporos.research.scans.shock_reversal import ShockReversalParameters
from emporos.research.scans.vix_shock_reversal import (
    VixReversalParameters,
    declared_arms,
    vix_shock_reversal_scan,
)

START = date(2020, 1, 1)


def openings(values: list[int]) -> dict[date, Decimal]:
    return {START + timedelta(days=i): Decimal(v) for i, v in enumerate(values)}


def gate(values: list[int], q: str = "0.8", lookback: int = 5, minimum: int = 3):  # type: ignore[no-untyped-def]
    return TrailingPercentileDays.from_openings(openings(values), Decimal(q), lookback, minimum)


class TestTrailingPercentile:
    def test_a_day_at_or_above_the_quantile_of_its_own_past_is_allowed(self) -> None:
        g = gate([10, 11, 12, 13, 14])  # day 5 (value 14): window 10..13, q0.8 -> rank 3 -> 13

        assert g.allows(START + timedelta(days=4))

    def test_a_day_below_it_is_not(self) -> None:
        g = gate([10, 11, 12, 13, 12])

        assert not g.allows(START + timedelta(days=4))

    def test_a_value_equal_to_the_threshold_is_allowed(self) -> None:
        assert gate([10, 11, 12, 13, 13]).allows(START + timedelta(days=4))

    def test_todays_value_is_not_in_its_own_baseline(self) -> None:
        # if 99 were in its own window the quantile would be 99 and it would still pass; the check
        # is that a huge value does not raise ITS OWN bar for the next day only through history
        g = gate([10, 10, 10, 99, 50])

        assert g.allows(START + timedelta(days=3))  # 99 vs window 10,10,10
        assert not g.allows(START + timedelta(days=4))  # 50 vs window 10,10,10,99 (q0.8 -> 99)

    def test_too_little_history_allows_nothing(self) -> None:
        g = gate([10, 11], minimum=3)

        assert g.days == frozenset()

    def test_the_lookback_forgets_old_sessions(self) -> None:
        # a spike long ago must not keep the bar high: window is the last 3 sessions only
        g = gate([1000, 10, 10, 10, 11], lookback=3, minimum=3)

        assert g.allows(START + timedelta(days=4))

    def test_out_of_range_parameters_are_refused(self) -> None:
        with pytest.raises(ValueError, match="strictly between"):
            gate([1, 2, 3], q="1")
        with pytest.raises(ValueError, match="minimum"):
            gate([1, 2, 3], minimum=9, lookback=5)


class TestSessionOpenings:
    def test_only_the_0915_bar_gives_a_sessions_opening(self) -> None:
        bars = session(MONDAY, open_="102", at_hour="101", close="99")

        assert session_openings(bars) == {MONDAY: Decimal(102)}

    def test_a_session_without_its_first_bar_has_no_opening(self) -> None:
        bars = session(MONDAY, open_="102", at_hour="101", close="99", skip_bar=1)

        assert session_openings(bars) == {}


class Recording:
    def __init__(self, intent: ScanIntent) -> None:
        self.intent = intent
        self.seen = 0

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        self.seen += 1
        return self.intent


class Days:
    def __init__(self, *allowed: date) -> None:
        self._allowed = set(allowed)

    def allows(self, day: date) -> bool:
        return day in self._allowed


class TestDayGatedRules:
    BARS = session(MONDAY, open_="102", at_hour="101", close="99")

    def test_an_entry_on_an_allowed_day_passes(self) -> None:
        rules = DayGatedRules(Recording(EntryIntent(OrderSide.SELL)), Days(MONDAY))

        assert rules.observe(self.BARS[0], live=True, held=None, can_afford=True) == EntryIntent(
            OrderSide.SELL
        )

    def test_an_entry_on_a_refused_day_is_dropped_but_the_inner_rules_still_see_the_bar(
        self,
    ) -> None:
        inner = Recording(EntryIntent(OrderSide.SELL))
        rules = DayGatedRules(inner, Days())

        assert rules.observe(self.BARS[0], live=True, held=None, can_afford=True) is None
        assert inner.seen == 1

    def test_no_intent_stays_no_intent(self) -> None:
        rules = DayGatedRules(Recording(None), Days(MONDAY))

        assert rules.observe(self.BARS[0], live=True, held=None, can_afford=True) is None


class TestVixGatedReversal:
    EXECUTION = ScanExecution(Decimal(50_000))
    PARAMS = VixReversalParameters(
        ShockReversalParameters(Decimal("1.0"), Decimal("0.5")), Decimal("0.8")
    )

    def trades(self, allowed: tuple[date, ...]):  # type: ignore[no-untyped-def]
        scan = vix_shock_reversal_scan(self.PARAMS, self.EXECUTION, Days(*allowed))
        return scan.scan("NSE:1", gap_up(at_hour="101"))

    def test_the_reversal_trades_on_a_high_vix_day(self) -> None:
        (trade,) = self.trades((MONDAY,))

        assert (trade.day, trade.side) == (MONDAY, OrderSide.SELL)

    def test_the_same_setup_on_a_calm_day_does_not_trade(self) -> None:
        assert self.trades(()) == []

    def test_only_the_gated_day_matters_not_the_day_before(self) -> None:
        assert self.trades((FRIDAY,)) == []


class TestGrid:
    def test_declared_arms_are_the_full_product(self) -> None:
        grid = {
            "gap_threshold_pct": ("1.0", "1.5", "2.0"),
            "retrace_min": ("0.25",),
            "vix_min_percentile": ("0.6", "0.8"),
        }
        assert len(declared_arms(grid)) == 6

    def test_an_undeclared_parameter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            declared_arms({"gap_threshold_pct": ("1",)})

    def test_a_point_round_trips(self) -> None:
        assert VixReversalParameters.from_point(TestVixGatedReversal.PARAMS.as_point()) == (
            TestVixGatedReversal.PARAMS
        )

    def test_a_percentile_outside_the_open_unit_interval_is_refused(self) -> None:
        with pytest.raises(ValueError, match="strictly between"):
            VixReversalParameters(ShockReversalParameters(Decimal(1), Decimal("0.5")), Decimal(1))

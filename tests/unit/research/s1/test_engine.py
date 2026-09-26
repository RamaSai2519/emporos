"""EM-219 S1: the fill rule against the Decimal implementation, sizing, one position at a time, the
risk limits, the control and the metrics, on hand-built sessions."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import numpy as np
import pytest
from tests.unit.eventtrader.replay.fakes import FakeMarket, bar, flat_day

from emporos.core.clock import IST
from emporos.eventtrader.replay.fills import EntryFiller, NoEntry
from emporos.eventtrader.replay.records import Scenario, TradeLeg
from emporos.eventtrader.stages.models import Side
from emporos.research.s1.arms import Arm, cash_arms, option_arms
from emporos.research.s1.bars import DayBars, MemoryBarStore, atr_before, sessions_from
from emporos.research.s1.control import RandomEntries
from emporos.research.s1.costs import CostCurve
from emporos.research.s1.engine import BookEngine, BookLimits
from emporos.research.s1.exit_policies import ExitReason
from emporos.research.s1.fills import Fill, Miss, marketable_limit, try_entry
from emporos.research.s1.metrics import Bars, control_p, measure
from emporos.research.s1.signals import Signal, signals_by_day
from emporos.research.scans.base import ScanExecution

D = date(2024, 3, 4)
X = "NSE:1"


class FlatExact:
    """A cost model of 0.1% (benchmark) and 0.2% (adverse) of the notional, both sides in."""

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        rate = Decimal("0.001") if scenario is Scenario.BENCHMARK else Decimal("0.002")
        return rate * leg.entry_price * leg.quantity


def curves() -> dict[int, CostCurve]:
    return {1: CostCurve(FlatExact(), Side.LONG), -1: CostCurve(FlatExact(), Side.SHORT)}


def day_of(rows: list[tuple[float, float, float, float]], volume: float = 1e6) -> DayBars:
    n = len(rows)
    return DayBars(
        np.array([9 * 60 + 20 + 5 * i for i in range(n)]),
        np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows]), np.array([r[3] for r in rows]),
        np.full(n, volume),
    )  # fmt: skip


def flat_rows(n: int = 75, price: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(price, price, price, price)] * n


def store(rows: list[tuple[float, float, float, float]], day: date = D) -> MemoryBarStore:
    return MemoryBarStore({(X, day): day_of(rows)})


ARM = Arm("cash", "one_to_one", 1, 0.005, "lock_fixed")


def engine(bars: MemoryBarStore, limits: BookLimits | None = None) -> BookEngine:
    return BookEngine(bars, curves(), limits)


class TestFillRule:
    def test_it_matches_the_decimal_implementation_on_random_bars(self) -> None:
        rng = random.Random(3)
        execution = ScanExecution(position_value=Decimal(50000))
        for case in range(300):
            price = rng.uniform(50, 500)
            rows = flat_rows(75, round(price, 2))
            k = rng.randrange(0, 40)
            o = round(price * rng.uniform(0.996, 1.004), 2)
            hi = round(max(o, price) * rng.uniform(1.0, 1.003), 2)
            lo = round(min(o, price) * rng.uniform(0.997, 1.0), 2)
            rows[k + 1] = (o, hi, lo, round(rng.uniform(lo, hi), 2))
            volume = rng.choice([50, 500, 5_000, 1_000_000])
            quantity = rng.choice([1, 20, 200])
            buying = rng.random() < 0.5
            candles = flat_day(X, D, round(price, 2), volume)
            candles[k + 1] = bar(X, D, *_hm(k + 1), *rows[k + 1], volume)  # type: ignore[arg-type]
            market = FakeMarket([D])
            market.put_day(X, D, candles)
            decision = candles[k].closes_at
            theirs = EntryFiller(market, execution).fill(
                X, Side.LONG if buying else Side.SHORT, decision, quantity
            )
            day = DayBars(
                np.array([9 * 60 + 20 + 5 * i for i in range(75)]),
                np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
                np.array([r[2] for r in rows]), np.array([r[3] for r in rows]),
                np.full(75, float(volume)),
            )  # fmt: skip
            mine = try_entry(day, 9 * 60 + 20 + 5 * k, buying, quantity)
            if isinstance(theirs, NoEntry):
                expected = {
                    NoEntry.NOT_THROUGH: Miss.NOT_THROUGH,
                    NoEntry.NO_VOLUME: Miss.NO_VOLUME,
                }
                assert mine is expected[theirs], case
            else:
                assert isinstance(mine, Fill) and mine.index == theirs.bar_index, case
                assert mine.reference == pytest.approx(float(theirs.reference))

    def test_the_limit_is_rounded_to_the_tick_so_it_stays_marketable(self) -> None:
        assert marketable_limit(100.0, True) == pytest.approx(100.05)
        assert marketable_limit(100.0, False) == pytest.approx(99.95)
        assert marketable_limit(1000.0, True) == pytest.approx(1000.5)

    def test_no_entry_after_1445(self) -> None:
        day = day_of(flat_rows())

        assert try_entry(day, 14 * 60 + 50, True, 1) is Miss.LATE


def _hm(index: int) -> tuple[int, int]:
    minute = 9 * 60 + 15 + 5 * index
    return minute // 60, minute % 60


def signal(minute: int, direction: int = 1, day: date = D, name: str = "AAA") -> Signal:
    return Signal(X, name, day, minute, direction)


class TestSizingAndTheTrade:
    def test_the_stop_caps_the_loss_and_the_position_is_sized_to_it(self) -> None:
        # target = 0.1% cost + 0.5% = 0.6%; one-to-one stop 0.6%; Rs 2,000 / 0.6% = Rs 333,333,
        # far above the Rs 80,000 size
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)  # the entry bar: strictly through the 100.05 limit
        rows[4] = (100.1, 100.1, 99.0, 99.2)  # falls through the stop at 99.4
        bars = store(rows)

        result = engine(bars).run(ARM, [D], {D: [signal(9 * 60 + 30)]})

        [trade] = result.trades
        assert trade.quantity == 800  # 80,000 / 100 at 1x: the size cap binds, not the risk cap
        assert trade.reason is ExitReason.STOP and trade.exit_price == pytest.approx(99.4)
        assert trade.gross == pytest.approx(-0.6 * 800)

    def test_leverage_and_the_risk_cap_bind_where_they_should(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        arm = Arm("cash", "half", 5, 0.005, "lock_fixed")  # stop 0.3%: Rs 2,000 / 0.3% = 666,667

        result = engine(store(rows)).run(arm, [D], {D: [signal(9 * 60 + 30)]})

        [trade] = result.trades
        assert trade.quantity == 4000  # 400,000 (5x) binds, the loss cap does not

        tight = Arm("cash", "atr", 5, 0.005, "lock_fixed")
        wide_rows = [(100.0, 101.0, 99.0, 100.0)] * 75  # ATR 2.0: a 2% stop
        wide_rows[3] = (100, 100.2, 99.9, 100.1)
        [capped] = engine(store(wide_rows)).run(tight, [D], {D: [signal(9 * 60 + 30)]}).trades
        assert capped.quantity == 1000  # Rs 2,000 / 2% = Rs 100,000, below the 400,000 leverage cap

    def test_a_target_then_a_trail_pays_more_than_the_target(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 101.0, 100.1, 100.9)  # touches 100.6: the trail starts, best 101.0
        rows[5] = (100.9, 103.0, 100.9, 102.9)  # best 103.0, stop 102.69 from the next bar
        rows[6] = (102.9, 102.95, 102.5, 102.6)  # falls through it
        result = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30)]})

        [trade] = result.trades
        assert trade.trailed and trade.reason is ExitReason.TRAIL
        assert trade.exit_price == pytest.approx(103.0 * 0.997)

    def test_a_short_is_a_sell_first(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.1, 99.8, 99.9)  # strictly through 99.95
        rows[4] = (99.9, 99.9, 99.0, 99.1)  # touches the 0.6% target
        rows[5] = (99.1, 99.2, 98.9, 99.0)
        rows[6] = (99.0, 99.6, 98.9, 99.5)  # trail stop
        result = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30, -1)]})

        [trade] = result.trades
        assert trade.direction == -1 and trade.gross > 0

    def test_no_stop_distance_means_no_trade(self) -> None:
        arm = Arm("cash", "atr", 1, 0.005, "lock_fixed")  # a flat market has an ATR of zero

        result = engine(store(flat_rows())).run(arm, [D], {D: [signal(9 * 60 + 30)]})

        assert result.trades == [] and result.skipped["no_stop"] == 1


class TestTheBook:
    def flat_then_moves(self) -> MemoryBarStore:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)  # the first trade stops out at bar 4 (ends 09:45)
        rows[10] = (99.4, 99.6, 99.3, 99.5)
        return store(rows)

    def test_one_position_at_a_time_signals_while_open_are_skipped_and_counted(self) -> None:
        bars = self.flat_then_moves()
        signals = [signal(9 * 60 + 30), signal(9 * 60 + 35)]

        result = engine(bars).run(ARM, [D], {D: signals})

        assert len(result.trades) == 1 and result.skipped["busy"] == 1 and result.signals == 2

    def test_a_signal_at_the_exit_bar_can_open_the_next_position(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)  # exits at the bar ending 09:45
        rows[10] = (99.2, 99.3, 99.0, 99.2)
        result = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30), signal(9 * 60 + 45)]})

        assert result.skipped["busy"] == 0
        assert result.skipped["not_through"] + len(result.trades) == 2

    def test_a_missed_fill_is_counted_and_does_not_hold_the_book(self) -> None:
        rows = flat_rows()
        rows[3] = (101.0, 101.5, 101.0, 101.2)  # trades wholly above the 100.05 limit
        result = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30)]})

        assert result.trades == [] and result.skipped["not_through"] == 1

    def test_the_daily_loss_limit_stops_new_entries_that_day(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)  # a Rs 480+ loss on 800 shares
        limits = BookLimits(daily_loss=300.0)

        result = engine(store(rows), limits).run(
            ARM, [D], {D: [signal(9 * 60 + 30), signal(10 * 60)]}
        )

        assert len(result.trades) == 1 and result.skipped["daily_limit"] == 1

    def test_the_total_loss_kills_the_arm_on_that_date(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)
        second = date(2024, 3, 5)
        bars = MemoryBarStore({(X, D): day_of(rows), (X, second): day_of(rows)})
        limits = BookLimits(total_loss=300.0, daily_loss=1e9)

        result = engine(bars, limits).run(
            ARM, [D, second], {D: [signal(9 * 60 + 30)], second: [signal(9 * 60 + 30, day=second)]}
        )

        assert result.killed_on == D and len(result.trades) == 1
        assert result.skipped["killed"] == 1


class TestControlAndMetrics:
    def test_the_control_keeps_each_entrys_instrument_and_day_and_is_reproducible(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)
        [trade] = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30)]}).trades

        first = RandomEntries().signals(ARM.name, 7, [trade])
        again = RandomEntries().signals(ARM.name, 7, [trade])
        other = RandomEntries().signals(ARM.name, 8, [trade])

        assert first == again and first != other
        [s] = first[D]
        assert (
            s.instrument_id == X
            and 9 * 60 + 20 <= s.minute <= 14 * 60 + 45
            and s.direction in (1, -1)
        )
        assert s.minute % 5 == 0

    def test_the_p_value_counts_control_runs_at_least_as_good(self) -> None:
        assert control_p(10.0, [1.0, 2.0, 30.0]) == pytest.approx((1 + 1) / 4)
        assert control_p(10.0, [1.0] * 199) == pytest.approx(1 / 200)

    def test_metrics_and_the_declared_bars(self) -> None:
        rows = flat_rows()
        rows[3] = (100, 100.2, 99.9, 100.1)
        rows[4] = (100.1, 100.1, 99.0, 99.2)
        result = engine(store(rows)).run(ARM, [D], {D: [signal(9 * 60 + 30)]})

        m = measure(result, [D, date(2024, 3, 5)])
        why = m.failures(Bars(), None)

        assert m.trades == 1 and m.net_adverse < 0 and m.win_rate == 0.0
        assert any("net at adverse" in w for w in why) and any("fewer than 100" in w for w in why)
        assert any("control p" in w for w in why)

    def test_a_passing_arm_passes_every_bar(self) -> None:
        from emporos.research.s1.metrics import ArmMetrics

        good = ArmMetrics(150, 9e3, 8e3, 0.6, 3.4, 4_000.0, 0.75, 12, None)

        assert good.failures(Bars(), 0.01) == []
        assert good.failures(Bars(), 0.2) != []


class TestArmsAndBars:
    def test_thirty_six_cash_arms_and_eighteen_option_arms(self) -> None:
        assert len(cash_arms()) == 36 and len({a.name for a in cash_arms()}) == 36
        assert len(option_arms()) == 18 and len({a.name for a in option_arms()}) == 18

    def test_atr_uses_the_last_fourteen_bars_and_falls_back_to_the_previous_session(self) -> None:
        rows = [(100.0, 101.0, 99.0, 100.0)] * 75
        prior = date(2024, 3, 1)
        bars = MemoryBarStore({(X, D): day_of(rows), (X, prior): day_of(rows)})

        assert atr_before(bars, X, D, 9 * 60 + 40) == pytest.approx(
            2.0
        )  # 4 bars today + prior tail
        assert atr_before(MemoryBarStore({}), X, D, 9 * 60 + 40) == 0.0

    def test_sessions_are_built_from_candles(self) -> None:
        sessions = sessions_from(flat_day(X, D, 100))

        assert list(sessions) == [D] and len(sessions[D]) == 75
        assert int(sessions[D].closes_at[0]) == 9 * 60 + 20 and IST is not None


class TestCostCurve:
    def test_the_curve_reproduces_a_flat_model_and_interpolates_between_probes(self) -> None:
        curve = CostCurve(FlatExact(), Side.LONG)

        assert curve.fraction(80_000, Scenario.BENCHMARK) == pytest.approx(0.001)
        assert curve.cost(123_456, Scenario.ADVERSE) == pytest.approx(123_456 * 0.002)
        assert curve.fraction(1, Scenario.BENCHMARK) == pytest.approx(0.001)  # clamped below
        assert curve.fraction(1e9, Scenario.ADVERSE) == pytest.approx(0.002)  # and above


def test_signals_group_by_day() -> None:
    grouped = signals_by_day([signal(560), signal(565, day=date(2024, 3, 5))])

    assert sorted(grouped) == [D, date(2024, 3, 5)]

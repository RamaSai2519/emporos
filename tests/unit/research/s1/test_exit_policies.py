"""EM-219 S1: the exit policies on hand-built bars, every rule of the declaration pinned."""

from __future__ import annotations

import pytest

from emporos.research.s1.exit_policies import (
    AtrStop,
    AtrTrail,
    Bar,
    ExitPolicy,
    ExitReason,
    HalfPeakTrail,
    HalfTargetStop,
    LockFixedTrail,
    OneToOneStop,
    Position,
    TargetStop,
    TargetThenTrail,
    run_exit,
)

LONG = Position(1, 100.0, atr=0.5, cost=0.002)
SHORT = Position(-1, 100.0, atr=0.5, cost=0.002)


def bar(minute: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(minute, o, h, low, c)


def bars(*rows: tuple[float, float, float, float]) -> list[Bar]:
    return [bar(9 * 60 + 25 + 5 * i, *row) for i, row in enumerate(rows)]


class TestFixedTargetAndStop:
    policy = TargetStop(stop=0.01, target=0.02)

    def test_the_target_is_a_fill_at_the_target(self) -> None:
        out = run_exit(
            self.policy, LONG, bars((100, 100.5, 99.8, 100.2), (100.2, 102.5, 100.1, 102))
        )

        assert out is not None
        assert (out.price, out.reason) == (pytest.approx(102.0), ExitReason.TARGET)

    def test_a_bar_touching_both_is_the_stop(self) -> None:
        out = run_exit(self.policy, LONG, bars((100, 102.5, 98.5, 100)))

        assert out is not None
        assert (out.price, out.reason) == (pytest.approx(99.0), ExitReason.STOP)

    def test_a_gap_through_the_stop_fills_at_the_open(self) -> None:
        out = run_exit(self.policy, LONG, bars((98.0, 98.5, 97.5, 98.2)))

        assert out is not None and out.price == 98.0 and out.reason is ExitReason.STOP

    def test_a_gap_over_the_target_fills_at_the_open(self) -> None:
        out = run_exit(self.policy, LONG, bars((103.0, 103.5, 102.5, 103.2)))

        assert out is not None and out.price == 103.0 and out.reason is ExitReason.TARGET

    def test_a_short_mirrors_a_long(self) -> None:
        out = run_exit(self.policy, SHORT, bars((100, 100.5, 97.5, 98)))

        assert out is not None
        assert (out.price, out.reason) == (pytest.approx(98.0), ExitReason.TARGET)
        stopped = run_exit(self.policy, SHORT, bars((100, 101.5, 99.9, 101)))
        assert stopped is not None and stopped.price == pytest.approx(101.0)

    def test_nothing_touched_squares_off_at_the_last_bar_by_1515(self) -> None:
        rows = [
            bar(15 * 60 + 5, 100, 100.3, 99.8, 100.1),
            bar(15 * 60 + 15, 100.1, 100.4, 99.9, 100.2),
            bar(15 * 60 + 20, 100.2, 105, 90, 95),
        ]
        out = run_exit(self.policy, LONG, rows)

        assert out is not None
        assert (out.price, out.closes_at, out.reason) == (
            100.2,
            15 * 60 + 15,
            ExitReason.SQUARE_OFF,
        )

    def test_no_bar_by_the_squareoff_is_no_exit(self) -> None:
        assert run_exit(self.policy, LONG, []) is None
        assert run_exit(self.policy, LONG, [bar(15 * 60 + 20, 100, 101, 99, 100)]) is None


def trail(stop: object, trail_rule: object, target: float = 0.01) -> ExitPolicy:
    return TargetThenTrail(target, stop, trail_rule)  # type: ignore[arg-type]


class TestStops:
    def test_one_to_one_half_and_atr_distances(self) -> None:
        for stop, expected in (
            (OneToOneStop(), 0.01),
            (HalfTargetStop(), 0.005),
            (AtrStop(), 0.005),
        ):
            policy = trail(stop, LockFixedTrail(0.003))
            out = run_exit(policy, LONG, bars((100, 100.2, 100 * (1 - expected) - 0.01, 99.5)))
            assert out is not None and out.price == pytest.approx(100 * (1 - expected))

    def test_an_atr_stop_needs_an_atr(self) -> None:
        flat = Position(1, 100.0, atr=0.0, cost=0.002)

        with pytest.raises(ValueError):
            run_exit(trail(AtrStop(), LockFixedTrail(0.003)), flat, bars((100, 101, 99, 100)))


class TestTrails:
    def test_the_target_does_not_close_the_trade_the_trail_takes_over(self) -> None:
        # bar 1 touches the 1% target (best 101.5); its stop applies from bar 2, which falls
        # through it
        policy = trail(OneToOneStop(), LockFixedTrail(0.003))
        out = run_exit(policy, LONG, bars((100, 101.5, 99.9, 101.4), (101.4, 101.6, 100.5, 100.9)))

        assert out is not None and out.reason is ExitReason.TRAIL and out.trailed
        assert out.price == pytest.approx(
            101.5 * 0.997
        )  # best less the gap, above the locked entry + T

    def test_the_new_stop_applies_from_the_next_bar_not_the_bar_that_set_it(self) -> None:
        policy = trail(OneToOneStop(), LockFixedTrail(0.003))
        # bar 1 touches the target AND falls to 100.4 (above the 99 stop): no exit that bar
        out = run_exit(
            policy, LONG, bars((100, 101.2, 100.4, 100.8), (101.3, 101.4, 101.1, 101.35))
        )

        assert out is not None and out.reason is ExitReason.SQUARE_OFF and out.trailed

    def test_lock_fixed_trails_the_best_price_by_its_gap(self) -> None:
        policy = trail(OneToOneStop(), LockFixedTrail(0.003))
        rows = bars(
            (100, 101.2, 100.9, 101.1), (101.1, 103.0, 101.05, 102.9), (102.9, 103.0, 102.6, 102.7)
        )
        out = run_exit(policy, LONG, rows)

        assert out is not None
        assert out.price == pytest.approx(103.0 * 0.997)  # best 103.0 less 0.3%

    def test_half_peak_keeps_half_the_best_gain_and_never_less_than_the_cost(self) -> None:
        policy = trail(OneToOneStop(), HalfPeakTrail())
        rows = bars(
            (100, 101.2, 100.9, 101.1), (101.1, 104.0, 101.0, 103.5), (103.5, 103.6, 101.9, 102.0)
        )
        out = run_exit(policy, LONG, rows)

        assert out is not None and out.price == pytest.approx(100 + 4.0 / 2)  # half of the 4.0 gain
        small = trail(OneToOneStop(), HalfPeakTrail(), target=0.002)
        early = run_exit(
            small, LONG, bars((100, 100.25, 99.9, 100.2), (100.3, 100.31, 100.1, 100.2))
        )
        assert early is not None and early.price == pytest.approx(100.2)  # the cost floor: 0.2%

    def test_atr_trail_follows_best_less_one_atr_never_below_entry_plus_cost(self) -> None:
        policy = trail(OneToOneStop(), AtrTrail())
        rows = bars((100, 102.0, 100.9, 101.8), (101.8, 102.0, 101.4, 101.5))
        out = run_exit(policy, LONG, rows)

        assert out is not None and out.price == pytest.approx(101.5)  # 102.0 - 0.5
        low_atr = Position(1, 100.0, atr=5.0, cost=0.002)
        floored = run_exit(
            policy, low_atr, bars((100, 101.2, 100.9, 101.1), (101.1, 101.2, 100.1, 100.3))
        )
        assert floored is not None and floored.price == pytest.approx(100.2)

    def test_a_gap_through_the_trailing_stop_fills_at_the_open(self) -> None:
        policy = trail(OneToOneStop(), LockFixedTrail(0.003))
        out = run_exit(policy, LONG, bars((100, 101.5, 99.9, 101.4), (100.2, 100.6, 100.0, 100.4)))

        assert out is not None and out.price == 100.2 and out.reason is ExitReason.TRAIL

    def test_a_stop_only_ever_tightens(self) -> None:
        policy = trail(OneToOneStop(), HalfPeakTrail())
        rows = bars(
            (100, 102.0, 100.9, 101.9), (101.9, 101.95, 101.6, 101.7), (101.7, 101.8, 100.95, 101.0)
        )
        out = run_exit(policy, LONG, rows)

        assert out is not None and out.price == pytest.approx(
            101.0
        )  # half of 2.0, not loosened by the dip

    def test_a_short_trail_mirrors_a_long(self) -> None:
        policy = trail(OneToOneStop(), LockFixedTrail(0.003))
        out = run_exit(policy, SHORT, bars((100, 100.1, 98.5, 98.6), (98.6, 99.2, 98.4, 99.1)))

        assert out is not None and out.reason is ExitReason.TRAIL
        assert out.price == pytest.approx(98.5 * 1.003)  # best (lowest) plus the gap

    def test_nonsense_positions_are_refused(self) -> None:
        with pytest.raises(ValueError):
            Position(0, 100.0, 0.5, 0.002)
        with pytest.raises(ValueError):
            Position(1, -1.0, 0.5, 0.002)

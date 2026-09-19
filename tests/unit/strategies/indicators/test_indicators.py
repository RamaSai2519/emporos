"""Indicators are checked against series worked out by hand (or, for the long RSI series, computed
exactly with rational arithmetic in a separate script) — never against another library at runtime.
Each hand-worked case shows its arithmetic."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Context, Decimal, localcontext
from typing import ClassVar

import pytest

from emporos.strategies.indicators import (
    AverageTrueRange,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
    SimpleMovingAverage,
)

D = Decimal


def _run(indicator: object, values: Iterable[str]) -> list[Decimal | None]:
    return [indicator.update(D(v)) for v in values]  # type: ignore[attr-defined]


def _places(values: Iterable[Decimal | None], places: str = "1e-20") -> list[str | None]:
    return [None if v is None else str(v.quantize(D(places))) for v in values]


class TestSimpleMovingAverage:
    def test_a_hand_worked_series(self) -> None:
        # period 3: (1+2+3)/3=2, (2+3+4)/3=3, (3+4+5)/3=4, (4+5+10)/3=19/3
        got = _run(SimpleMovingAverage(3), ["1", "2", "3", "4", "5", "10"])
        assert got[:5] == [None, None, D(2), D(3), D(4)]
        assert _places([got[5]]) == ["6.33333333333333333333"]

    def test_period_one_is_the_price_itself(self) -> None:
        assert _run(SimpleMovingAverage(1), ["7", "8.5"]) == [D(7), D("8.5")]

    def test_it_is_not_ready_until_the_window_fills(self) -> None:
        sma = SimpleMovingAverage(3)
        sma.update(D(1)), sma.update(D(2))
        assert not sma.ready and sma.value is None
        sma.update(D(3))
        assert sma.ready and sma.value == D(2)
        assert sma.period == 3

    def test_a_long_run_does_not_drift(self) -> None:
        sma = SimpleMovingAverage(4)
        for i in range(10_000):
            sma.update(D("100.05") + D(i % 7))
        # 9996 = 7 * 1428, so the last four offsets are 0, 1, 2, 3: mean 1.5 exactly
        assert sma.value == D("101.55")


class TestExponentialMovingAverage:
    def test_a_hand_worked_series_with_an_sma_seed(self) -> None:
        # period 3, alpha = 2/(3+1) = 0.5. Seed = (10+11+12)/3 = 11.
        # 13: 11 + 0.5*(13-11) = 12; 14: 12 + 0.5*(14-12) = 13; 10: 13 + 0.5*(10-13) = 11.5
        got = _run(ExponentialMovingAverage(3), ["10", "11", "12", "13", "14", "10"])
        assert got == [None, None, D(11), D(12), D(13), D("11.5")]

    def test_a_four_period_series_with_alpha_point_four(self) -> None:
        # alpha = 2/5 = 0.4. Seed = (22.27+22.19+22.08+22.17)/4 = 22.1775
        # 22.18 -> 22.1775 + 0.4*(0.0025)  = 22.1785
        # 22.13 -> 22.1785 + 0.4*(-0.0485) = 22.1591
        # 22.23 -> 22.1591 + 0.4*(0.0709)  = 22.18746
        got = _run(
            ExponentialMovingAverage(4),
            ["22.27", "22.19", "22.08", "22.17", "22.18", "22.13", "22.23"],
        )
        assert got[3:] == [D("22.1775"), D("22.1785"), D("22.1591"), D("22.18746")]

    def test_period_one_tracks_the_price(self) -> None:
        assert _run(ExponentialMovingAverage(1), ["5", "9", "2"]) == [D(5), D(9), D(2)]

    def test_readiness_and_period(self) -> None:
        ema = ExponentialMovingAverage(2)
        assert ema.update(D(1)) is None and not ema.ready and ema.value is None
        assert ema.update(D(3)) == D(2) and ema.ready and ema.period == 2

    def test_a_flat_series_stays_flat(self) -> None:
        assert set(_run(ExponentialMovingAverage(5), ["50"] * 30)[4:]) == {D(50)}


class TestRelativeStrengthIndex:
    def test_a_hand_worked_series(self) -> None:
        # period 3; closes 10, 11, 10, 12, 11, 13 -> changes +1, -1, +2, -1, +2
        # first averages: gain (1+0+2)/3 = 1, loss (0+1+0)/3 = 1/3; RS = 3; RSI = 100 - 100/4 = 75
        # -1: gain (1*2+0)/3 = 2/3, loss (1/3*2+1)/3 = 5/9; RS = 6/5; RSI = 100 - 100/2.2 = 54.54..
        # +2: gain (2/3*2+2)/3 = 10/9, loss (5/9*2+0)/3 = 10/27; RS = 3; RSI = 75
        got = _run(RelativeStrengthIndex(3), ["10", "11", "10", "12", "11", "13"])
        assert got[:3] == [None, None, None]
        assert _places(got[3:]) == [
            "75.00000000000000000000",
            "54.54545454545454545455",
            "75.00000000000000000000",
        ]

    def test_the_first_value_only_sets_the_baseline(self) -> None:
        rsi = RelativeStrengthIndex(2)
        rsi.update(D(10)), rsi.update(D(11))
        assert not rsi.ready
        rsi.update(D(12))
        assert rsi.ready and rsi.period == 2

    def test_only_gains_is_100_only_losses_is_0(self) -> None:
        assert _run(RelativeStrengthIndex(3), ["1", "2", "3", "4", "5"])[3:] == [D(100), D(100)]
        assert _run(RelativeStrengthIndex(3), ["5", "4", "3", "2", "1"])[3:] == [D(0), D(0)]

    def test_a_flat_market_is_neutral_by_convention(self) -> None:
        assert _run(RelativeStrengthIndex(3), ["9"] * 6)[3:] == [D(50)] * 3

    def test_a_fourteen_period_series_matches_exact_rational_arithmetic(self) -> None:
        # 33 closes, RSI(14). Expected values come from a separate exact (fractions) computation
        # of Wilder's method, rounded to 2 places.
        closes = (
            "44.34 44.09 44.15 43.61 44.33 44.83 45.10 45.42 45.84 46.08 45.89 46.03 45.61 46.28 "
            "46.28 46.00 46.03 46.41 46.22 45.64 46.21 46.25 45.71 46.45 45.78 45.35 44.03 44.18 "
            "44.22 44.57 43.42 42.66 43.13"
        ).split()
        expected = (
            "70.46 66.25 66.48 69.35 66.29 57.92 62.88 63.21 56.01 62.34 54.67 50.39 40.02 41.49 "
            "41.90 45.50 37.32 33.09 37.79"
        ).split()

        got = _run(RelativeStrengthIndex(14), closes)

        assert got[:14] == [None] * 14
        assert [str(v.quantize(D("0.01"))) for v in got[14:] if v is not None] == expected


class TestAverageTrueRange:
    BARS: ClassVar[list[tuple[str, str, str]]] = [
        ("10", "8", "9"),
        ("11", "9", "10"),
        ("14", "10", "13"),
        ("12", "9", "11"),
        ("20", "18", "19"),
    ]

    def test_a_hand_worked_series_including_a_gap(self) -> None:
        # period 2. Bar 1: TR = 10-8 = 2. Bar 2 (prev close 9): max(2, |11-9|, |9-9|) = 2 -> ATR 2
        # Bar 3 (prev 10): max(4, |14-10|, |10-10|) = 4 -> ATR (2*1+4)/2 = 3
        # Bar 4 (prev 13): max(3, |12-13|=1, |9-13|=4) = 4 -> ATR (3*1+4)/2 = 3.5  (the gap down)
        atr = AverageTrueRange(2)
        got = [atr.update(D(high), D(low), D(close)) for high, low, close in self.BARS]
        # Bar 5 (prev 11): max(2, |20-11|=9, |18-11|=7) = 9 -> ATR (3.5*1+9)/2 = 6.25  (the gap up)
        assert got == [None, D(2), D(3), D("3.5"), D("6.25")]

    def test_the_first_bars_range_is_high_minus_low(self) -> None:
        atr = AverageTrueRange(1)
        assert atr.update(D(15), D(12), D(14)) == D(3) and atr.ready and atr.period == 1

    def test_a_bar_with_high_below_low_is_refused(self) -> None:
        with pytest.raises(ValueError, match="high"):
            AverageTrueRange(2).update(D(9), D(10), D(9))


ALL: list[Callable[[], object]] = [
    lambda: SimpleMovingAverage(3),
    lambda: ExponentialMovingAverage(3),
    lambda: RelativeStrengthIndex(3),
]
SERIES = ["10.13", "11.07", "10.5", "12.2", "11.9", "13.01", "12.4", "12.88"]


@pytest.mark.parametrize("make", ALL)
def test_two_instances_fed_the_same_series_agree_exactly(make: Callable[[], object]) -> None:
    assert _run(make(), SERIES) == _run(make(), SERIES)


@pytest.mark.parametrize("make", ALL)
def test_results_do_not_depend_on_the_ambient_decimal_context(make: Callable[[], object]) -> None:
    baseline = _run(make(), SERIES)
    with localcontext(Context(prec=4)):
        assert _run(make(), SERIES) == baseline


def test_atr_ignores_the_ambient_decimal_context_too() -> None:
    bars = [("10.1", "8.3", "9.7"), ("11.9", "9.1", "10.4"), ("14.2", "10.6", "13.3")]

    def run() -> list[Decimal | None]:
        atr = AverageTrueRange(2)
        return [atr.update(D(high), D(low), D(close)) for high, low, close in bars]

    baseline = run()
    with localcontext(Context(prec=3)):
        assert run() == baseline


@pytest.mark.parametrize("period", [0, -1, 1.5, True, "3"])
def test_a_period_must_be_a_positive_integer(period: object) -> None:
    for kind in (
        SimpleMovingAverage,
        ExponentialMovingAverage,
        RelativeStrengthIndex,
        AverageTrueRange,
    ):
        with pytest.raises(ValueError, match="period"):
            kind(period)  # type: ignore[arg-type]


def test_floats_are_never_silently_accepted() -> None:
    for indicator in (
        SimpleMovingAverage(2),
        ExponentialMovingAverage(2),
        RelativeStrengthIndex(2),
    ):
        indicator.update(D(1))
        with pytest.raises(TypeError):
            indicator.update(1.5)  # type: ignore[arg-type]

"""EM-105: the arithmetic and the return ratios, verified against series worked by hand.

Nothing here compares with another library. Where a result involves a square root, the test checks
the SQUARE of the result against an exact fraction computed by hand (e.g. Sharpe^2 = 21)."""

from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, localcontext
from fractions import Fraction

import pytest

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.metrics.equity import DailyEquity, DailySeries
from emporos.backtest.metrics.ratios import RatioCalculator
from tests.support.backtest_metrics import curve, daily, point
from tests.support.strategies import T0

D = Decimal
TIGHT = D("1e-25")


def as_fraction(value: Decimal) -> Fraction:
    return Fraction(value)


class TestDecimalMath:
    def test_the_sample_standard_deviation_uses_n_minus_one(self) -> None:
        values = [D(x) for x in (2, 4, 4, 4, 5, 5, 7, 9)]  # mean 5, sum of squares 32
        stdev = DecimalMath.sample_stdev(values)

        assert abs(stdev**2 - D(32) / 7) < TIGHT  # 32 / (8 - 1); the population figure would be 4
        assert DecimalMath.mean(values) == D(5)

    def test_it_needs_enough_values(self) -> None:
        with pytest.raises(ValueError):
            DecimalMath.mean([])
        with pytest.raises(ValueError):
            DecimalMath.sample_stdev([D(1)])
        with pytest.raises(ValueError):
            DecimalMath.power(D(0), D(2))

    def test_results_do_not_depend_on_the_callers_decimal_context(self) -> None:
        values = [D("0.123456"), D("-0.654321"), D("0.111112")]

        def everything() -> tuple[Decimal, ...]:
            return (
                DecimalMath.mean(values),
                DecimalMath.sample_stdev(values),
                DecimalMath.divide(D(1), D(3)),
                DecimalMath.sqrt(D(2)),
                DecimalMath.power(D("1.089"), D("0.3")),
            )

        expected = everything()
        with localcontext(Context(prec=3)):
            assert everything() == expected
        assert len(str(expected[0])) > 10  # the reference itself is not rounded away

    def test_square_root_and_power(self) -> None:
        assert abs(DecimalMath.sqrt(D(2)) ** 2 - 2) < TIGHT
        assert abs(DecimalMath.power(D(2), D("0.5")) ** 2 - 2) < TIGHT
        assert DecimalMath.power(D(3), D(2)) == D(9)


class TestDailySeries:
    def test_the_last_point_of_each_indian_day_is_that_days_equity(self) -> None:
        points = [
            point("100", T0),
            point("103", T0.replace(hour=8)),  # 13:45 IST, day 1's last
            point("101", T0.replace(day=T0.day + 1)),
            point("99", T0.replace(day=T0.day + 1, hour=9)),  # day 2's last
        ]

        days = DailySeries().build(D(100), points)

        assert [(d.day, d.equity) for d in days] == [
            (date(2026, 1, 5), D(103)),
            (date(2026, 1, 6), D(99)),
        ]
        assert days[0].ret == D("0.03")  # from the starting cash
        assert abs(days[1].ret - (D(99) / D(103) - 1)) < TIGHT

    def test_a_point_after_18_30_utc_belongs_to_the_next_indian_day(self) -> None:
        late = T0.replace(hour=19)  # 00:30 IST the next day

        days = DailySeries().build(D(100), [point("100", T0), point("110", late)])

        assert [d.day for d in days] == [date(2026, 1, 5), date(2026, 1, 6)]

    def test_a_flat_day_returns_exactly_zero(self) -> None:
        days = DailySeries().build(D(100), curve(["100", "100"]))
        assert [d.ret for d in days] == [D(0), D(0)]

    def test_no_points_no_days(self) -> None:
        assert DailySeries().build(D(100), []) == ()


class TestSharpeAndSortino:
    # start 100 -> 110 -> 99 -> 108.9 : daily returns exactly +0.1, -0.1, +0.1
    DAYS = daily("100", ["110", "99", "108.9"])

    def test_the_returns_are_what_the_series_says(self) -> None:
        assert [d.ret for d in self.DAYS] == [D("0.1"), D("-0.1"), D("0.1")]

    def test_sharpe_squared_is_21(self) -> None:
        # mean = 1/30. sample variance = ((1/15)^2 + (2/15)^2 + (1/15)^2) / 2 = (6/225)/2 = 1/75.
        # Sharpe = (1/30) / sqrt(1/75) * sqrt(252)  =>  Sharpe^2 = (1/900) * 75 * 252 = 21
        ratios = RatioCalculator().calculate(D(100), self.DAYS)

        assert ratios.sharpe is not None
        assert abs(ratios.sharpe**2 - 21) < TIGHT

    def test_sortino_squared_is_84(self) -> None:
        # downside deviation = sqrt((0 + 0.01 + 0) / 3) = sqrt(1/300)
        # Sortino^2 = (1/900) * 300 * 252 = 84
        ratios = RatioCalculator().calculate(D(100), self.DAYS)

        assert ratios.sortino is not None
        assert abs(ratios.sortino**2 - 84) < TIGHT

    def test_a_risk_free_rate_lowers_both(self) -> None:
        # annual rf 2.52 -> daily 0.01. Excess mean = 1/30 - 1/100 = 7/300.
        # Sharpe^2 = (7/300)^2 * 75 * 252 = 10.29
        # Sortino: shortfalls (r - 0.01) are 0, -0.11, 0 -> semivariance 0.0121 / 3;
        #          Sortino^2 = (7/300)^2 / (0.0121 / 3) * 252 = 37044 / 1089
        ratios = RatioCalculator(risk_free_annual=D("2.52")).calculate(D(100), self.DAYS)

        assert ratios.sharpe is not None and ratios.sortino is not None
        assert abs(ratios.sharpe**2 - D("10.29")) < TIGHT
        expected = Fraction(37044, 1089)
        assert abs(as_fraction(ratios.sortino**2) - expected) < Fraction(1, 10**25)

    def test_annualisation_days_scale_the_ratios_by_their_square_root(self) -> None:
        base = RatioCalculator(annualisation_days=252).calculate(D(100), self.DAYS)
        weekly = RatioCalculator(annualisation_days=63).calculate(D(100), self.DAYS)

        assert base.sharpe is not None and weekly.sharpe is not None
        assert abs((weekly.sharpe / base.sharpe) ** 2 - D("0.25")) < TIGHT  # 63/252

    def test_no_variance_or_no_losing_day_means_no_ratio_not_infinity(self) -> None:
        flat = RatioCalculator().calculate(D(100), daily("100", ["101", "101.01"]))
        assert flat.sharpe is not None  # variance exists
        assert flat.sortino is None  # ...but there is no losing day to divide by

        constant = RatioCalculator().calculate(D(100), daily("100", ["100", "100", "100"]))
        assert constant.sharpe is None and constant.sortino is None

    def test_a_single_day_has_no_sharpe(self) -> None:
        assert RatioCalculator().calculate(D(100), daily("100", ["101"])).sharpe is None

    def test_no_days_at_all(self) -> None:
        ratios = RatioCalculator().calculate(D(100), [])
        assert (ratios.total_return, ratios.cagr, ratios.sharpe, ratios.daily_returns) == (
            0,
            None,
            None,
            0,
        )

    def test_a_bad_annualisation_is_refused(self) -> None:
        with pytest.raises(ValueError):
            RatioCalculator(annualisation_days=0)


class TestTotalReturnAndCagr:
    def test_total_return_is_end_over_start_minus_one(self) -> None:
        assert RatioCalculator().calculate(
            D(100), daily("100", ["110", "99", "108.9"])
        ).total_return == D("0.089")

    def test_a_year_that_ends_up_half_is_a_cagr_of_a_half(self) -> None:
        # 1 Jan to 31 Dec 2025 inclusive is 365 days = exactly one year
        days = daily("100", ["150"], first=date(2025, 1, 1))
        days.append(DailyEquity(date(2025, 12, 31), D(150), D(0)))

        ratios = RatioCalculator().calculate(D(100), days)

        assert ratios.years == D(1)
        assert ratios.cagr == D("0.5")

    def test_doubling_in_two_years_compounds_at_the_square_root_of_two(self) -> None:
        # 1 Jan 2024 to 30 Dec 2025 inclusive = 366 + 364 = 730 days = exactly two years
        days = daily("100", ["100"], first=date(2024, 1, 1))
        days.append(DailyEquity(date(2025, 12, 30), D(200), D(1)))

        ratios = RatioCalculator().calculate(D(100), days)

        assert ratios.years == D(2)
        assert ratios.cagr is not None and abs((1 + ratios.cagr) ** 2 - 2) < TIGHT

    def test_losing_everything_is_minus_one_and_going_negative_has_no_cagr(self) -> None:
        wiped = RatioCalculator().calculate(D(100), daily("100", ["0"]))
        assert wiped.cagr == D(-1)
        negative = RatioCalculator().calculate(D(100), daily("100", ["-5"]))
        assert negative.cagr is None

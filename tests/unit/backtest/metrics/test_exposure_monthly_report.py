"""EM-105: exposure, turnover, the monthly table, and the whole report end to end."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from emporos.backtest.metrics.document import MetricsDocument
from emporos.backtest.metrics.exposure import ExposureAnalyzer, TurnoverAnalyzer
from emporos.backtest.metrics.monthly import MonthlyTable
from emporos.backtest.metrics.report import MetricsCalculator, MetricsSettings
from emporos.domain.money import Money
from tests.support.backtest_metrics import daily, point, trade

D = Decimal


def at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 6, 0, tzinfo=UTC)  # 11:30 IST


class TestExposure:
    def test_time_in_market_and_average_exposure_by_hand(self) -> None:
        # four points: flat, 50 of 100 at work, 60 of 100 at work, flat again
        curve = [
            point("100", at(1, 5)),
            point("100", at(1, 6), exposure="50", open_positions=1),
            point("100", at(1, 7), exposure="60", open_positions=2),
            point("100", at(1, 8)),
        ]

        stats = ExposureAnalyzer().analyze(curve)

        assert stats.time_in_market == D("0.5")
        assert stats.average_exposure == D("0.275")  # (0 + 0.5 + 0.6 + 0) / 4

    def test_no_curve_no_claim(self) -> None:
        stats = ExposureAnalyzer().analyze([])
        assert (stats.time_in_market, stats.average_exposure) == (None, None)


class TestTurnover:
    def test_notional_over_average_equity_and_per_year(self) -> None:
        # 500,000 traded on an account averaging 100,000: 5x over a run of half a year: 10x a year
        stats = TurnoverAnalyzer().analyze(
            Money.of("500000"), daily("100000", ["100000", "100000"]), D("0.5")
        )
        assert (stats.turnover, stats.annualised_turnover) == (D(5), D(10))
        assert stats.traded_notional == Money.of("500000")

    def test_the_average_is_over_all_days_not_the_first(self) -> None:
        # equity 100,000 then 200,000 averages 150,000: 300,000 traded is 2x
        stats = TurnoverAnalyzer().analyze(
            Money.of("300000"), daily("100000", ["100000", "200000"]), D(1)
        )
        assert stats.turnover == D(2)

    def test_it_is_undefined_without_days_or_with_an_empty_account(self) -> None:
        assert TurnoverAnalyzer().analyze(Money.of("1"), [], D(1)).turnover is None
        assert TurnoverAnalyzer().analyze(Money.of("1"), daily("1", ["0"]), D(1)).turnover is None


class TestMonthlyTable:
    def test_months_run_month_end_to_month_end_from_the_starting_cash(self) -> None:
        days = daily("100", ["101", "102"], first=date(2026, 1, 5))  # January ends at 102
        days += daily("102", ["99.96"], first=date(2026, 2, 10))  # February ends at 99.96

        rows = MonthlyTable().build(D(100), days)

        assert [(r.month, r.ret, r.pnl.amount) for r in rows] == [
            ("2026-01", D("0.02"), D(2)),
            ("2026-02", D("-0.02"), D("-2.04")),  # 99.96 / 102 - 1 = -0.02 exactly
        ]

    def test_a_month_without_trading_days_is_absent_and_the_next_spans_the_gap(self) -> None:
        days = daily("100", ["110"], first=date(2026, 1, 5)) + daily(
            "110", ["121"], first=date(2026, 3, 2)
        )

        rows = MonthlyTable().build(D(100), days)

        assert [r.month for r in rows] == ["2026-01", "2026-03"]
        assert rows[1].ret == D("0.1")

    def test_no_days_no_rows(self) -> None:
        assert MonthlyTable().build(D(100), []) == ()


class TestWholeReport:
    """A three-day account: 100 -> 110 -> 99 -> 108.9, over exactly one year (1 Jan to 31 Dec 2026
    inclusive is 365 days). Everything below follows by hand from those numbers."""

    def report(self):  # type: ignore[no-untyped-def]
        curve = [
            point("110", at(1, 1), exposure="55", open_positions=1),
            point("99", at(1, 2), exposure="99", open_positions=1),
            point("108.9", at(12, 31)),
        ]
        trades = [trade("10", 0), trade("-1", 10)]
        return MetricsCalculator().calculate(Money.of("100"), curve, trades, Money.of("300"))

    def test_the_headline_figures(self) -> None:
        r = self.report()

        assert r.trading_days == 3
        assert r.ending_equity == Money.of("108.9")
        assert r.returns.total_return == D("0.089")
        assert (r.returns.years, r.returns.cagr) == (D(1), D("0.089"))
        assert abs(r.returns.sharpe**2 - 21) < D("1e-25")
        # the deepest fall is 110 -> 99: (110 - 99) / 110 = 0.1, and 108.9 is still under 110
        assert r.drawdown.max_drawdown == D("0.1")
        assert r.drawdown.periods[0].recovered_at is None
        assert r.calmar == D("0.89")  # cagr 0.089 / max drawdown 0.1

    def test_the_rendered_document_is_exact_text(self) -> None:
        doc = MetricsDocument().render(self.report())

        assert doc["account"] == {
            "starting_cash": "100.00",
            "ending_equity": "108.90",
            "trading_days": 3,
        }
        assert doc["returns"] == {
            "total_return": "0.08900000",
            "cagr": "0.08900000",
            "years": "1.00000000",
            "sharpe": "4.58257569",  # sqrt(21)
            "sortino": "9.16515139",  # sqrt(84)
            "calmar": "0.89000000",
        }
        assert doc["drawdown"]["max_drawdown"] == "0.10000000"
        assert doc["trades"]["count"] == 2 and doc["trades"]["net_pnl"] == "9.00"
        assert doc["monthly_returns"] == [
            {"month": "2026-01", "return": "-0.01000000", "pnl": "-1.00"},
            {"month": "2026-12", "return": "0.10000000", "pnl": "9.90"},
        ]
        assert doc["exposure"]["time_in_market"] == "0.66666667"  # 2 of 3 points

    def test_the_document_is_plain_data_with_no_floats_and_is_stable(self) -> None:
        def leaves(node: object) -> list[object]:
            if isinstance(node, dict):
                return [leaf for value in node.values() for leaf in leaves(value)]
            if isinstance(node, list):
                return [leaf for value in node for leaf in leaves(value)]
            return [node]

        first = MetricsDocument().render(self.report())

        assert all(isinstance(leaf, str | int | None) for leaf in leaves(first))
        assert not any(isinstance(leaf, float) for leaf in leaves(first))
        assert first == MetricsDocument().render(self.report())

    def test_settings_are_carried_into_the_report_and_change_the_ratios(self) -> None:
        curve = [point("110", at(1, 1)), point("99", at(1, 2)), point("108.9", at(1, 3))]
        plain = MetricsCalculator().calculate(Money.of("100"), curve, [], Money.zero())
        rich = MetricsCalculator(MetricsSettings(risk_free_annual=D("2.52"))).calculate(
            Money.of("100"), curve, [], Money.zero()
        )

        assert rich.settings.risk_free_annual == D("2.52")
        assert rich.returns.sharpe < plain.returns.sharpe

    def test_an_empty_run_reports_nothing_rather_than_inventing_numbers(self) -> None:
        r = MetricsCalculator().calculate(Money.of("100"), [], [], Money.zero())
        doc = MetricsDocument().render(r)

        assert doc["account"]["ending_equity"] == "100.00"
        assert (doc["returns"]["sharpe"], doc["returns"]["cagr"], doc["trades"]["win_rate"]) == (
            None,
            None,
            None,
        )
        assert doc["monthly_returns"] == [] and doc["trades"]["count"] == 0

    def test_a_tiny_negative_that_rounds_to_zero_is_not_written_as_negative_zero(self) -> None:
        assert MetricsDocument.ratio(D("-0.000000001")) == "0.00000000"
        assert MetricsDocument.money(Money.of("-0.001")) == "0.00"

    def test_rounding_is_half_even_under_any_ambient_context(self) -> None:
        from decimal import Context, localcontext

        with localcontext(Context(prec=2)):
            assert MetricsDocument.ratio(D("0.123456785")) == "0.12345678"  # ...785 -> even 8
            assert MetricsDocument.money(Money.of("2.675")) == "2.68"  # .675 -> even? 7 is odd -> 8

"""`MetricsDocument` — the metrics report as plain JSON-able data, rounded the same way every time.

Money is written to the paisa (2 places), ratios and fractions to 8 places, always half-even, as
STRINGS: a float never appears, so a golden file compares exactly and diffs read cleanly. A
number that could not be computed (no losing trade for a profit factor, say) is `null`.
Fractions are fractions, not percentages: 0.25 is 25%.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from emporos.backtest.metrics.decimal_math import CONTEXT
from emporos.backtest.metrics.drawdown import DrawdownPeriod
from emporos.backtest.metrics.monthly import MonthlyReturn
from emporos.backtest.metrics.report import MetricsReport
from emporos.backtest.metrics.trades import TradeStatistics
from emporos.domain.money import Money
from emporos.strategies.regime import MarketRegimeClassifier

_PAISA = Decimal("0.01")
_PRICE = Decimal("0.0001")
_RATIO = Decimal("0.00000001")


class MetricsDocument:
    def render(self, report: MetricsReport) -> dict[str, Any]:
        returns, trades = report.returns, report.trades
        return {
            "conventions": {
                "annualisation_days": report.settings.annualisation_days,
                "risk_free_annual": self.ratio(report.settings.risk_free_annual),
                "returns": "daily, from end-of-day equity; fractions, not percent",
                "cagr_years": "calendar days first to last trading day inclusive / 365",
            },
            "account": {
                "starting_cash": self.money(report.starting_cash),
                "ending_equity": self.money(report.ending_equity),
                "trading_days": report.trading_days,
            },
            "returns": {
                "total_return": self.ratio(returns.total_return),
                "cagr": self.ratio(returns.cagr),
                "years": self.ratio(returns.years),
                "sharpe": self.ratio(returns.sharpe),
                "sortino": self.ratio(returns.sortino),
                "calmar": self.ratio(report.calmar),
            },
            "drawdown": {
                "max_drawdown": self.ratio(report.drawdown.max_drawdown),
                "count": report.drawdown.count,
                "longest_days": report.drawdown.longest_days,
                "worst_periods": [self._period(p) for p in report.drawdown.periods],
            },
            "trades": self._trade_stats(trades),
            "breakdowns": {
                "by_direction": self._grouped(report.by_direction),
                "by_instrument": self._grouped(report.by_instrument),
                "by_time_of_day": self._grouped(report.by_time_of_day),
                "by_regime": self._grouped(report.by_regime),
                "by_strategy": self._grouped(report.by_strategy),
                "by_direction_instrument": self._crossed(report.by_direction_instrument),
                "by_direction_time_of_day": self._crossed(report.by_direction_time_of_day),
                "by_direction_regime": self._crossed(report.by_direction_regime),
                "regime_classifier_version": MarketRegimeClassifier.VERSION
                if report.by_regime
                else None,
            },
            "exposure": {
                "time_in_market": self.ratio(report.exposure.time_in_market),
                "average_exposure": self.ratio(report.exposure.average_exposure),
            },
            "turnover": {
                "traded_notional": self.money(report.turnover.traded_notional),
                "turnover": self.ratio(report.turnover.turnover),
                "annualised_turnover": self.ratio(report.turnover.annualised_turnover),
            },
            "monthly_returns": [self._month(m) for m in report.monthly],
        }

    @classmethod
    def money(cls, value: Money | None) -> str | None:
        return None if value is None else cls._fixed(value.amount, _PAISA)

    @classmethod
    def price(cls, value: Money) -> str:
        """An average price, to a hundredth of a paisa."""
        return cls._fixed(value.amount, _PRICE)

    @classmethod
    def ratio(cls, value: Decimal | None) -> str | None:
        return None if value is None else cls._fixed(value, _RATIO)

    @staticmethod
    def _fixed(value: Decimal, places: Decimal) -> str:
        """Rounded under the metrics' own context, so an ambient one cannot change the text; a
        tiny negative that rounds to zero is written as zero, never as "-0.00"."""
        rounded = value.quantize(places, rounding=ROUND_HALF_EVEN, context=CONTEXT)
        return format(abs(rounded) if rounded == 0 else rounded, "f")

    def _period(self, period: DrawdownPeriod) -> dict[str, Any]:
        return {
            "peak_at": period.peak_at.isoformat(),
            "trough_at": period.trough_at.isoformat(),
            "recovered_at": None
            if period.recovered_at is None
            else period.recovered_at.isoformat(),
            "depth": self.ratio(period.depth),
        }

    def _month(self, month: MonthlyReturn) -> dict[str, Any]:
        return {"month": month.month, "return": self.ratio(month.ret), "pnl": self.money(month.pnl)}

    def _trade_stats(self, trades: TradeStatistics) -> dict[str, Any]:
        return {
            "count": trades.count,
            "wins": trades.wins,
            "losses": trades.losses,
            "breakeven": trades.breakeven,
            "win_rate": self.ratio(trades.win_rate),
            "gross_pnl": self.money(trades.gross_pnl),
            "fees": self.money(trades.fees),
            "net_pnl": self.money(trades.net_pnl),
            "gross_profit": self.money(trades.gross_profit),
            "gross_loss": self.money(trades.gross_loss),
            "profit_factor": self.ratio(trades.profit_factor),
            "average_trade": self.money(trades.average_trade),
            "average_win": self.money(trades.average_win),
            "average_loss": self.money(trades.average_loss),
            "expectancy": self.ratio(trades.expectancy),
            "max_consecutive_wins": trades.max_consecutive_wins,
            "max_consecutive_losses": trades.max_consecutive_losses,
        }

    def _grouped(self, groups: dict[str, TradeStatistics]) -> dict[str, dict[str, Any]]:
        return {name: self._trade_stats(stats) for name, stats in groups.items()}

    def _crossed(
        self, cross: dict[str, dict[str, TradeStatistics]]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        return {outer: self._grouped(inner) for outer, inner in cross.items()}

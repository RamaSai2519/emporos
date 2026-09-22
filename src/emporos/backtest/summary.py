"""`BacktestSummary` — the human-readable face of a backtest document.

Rendered from the SAME document the golden files hold, so what a person reads and what a test
compares cannot disagree. Fractions are shown as percentages here (the document keeps them as
fractions); every assumption is printed before any number, so a result is never read without it.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

_HUNDREDTH = Decimal("0.01")


class BacktestSummary:
    def render(self, doc: dict[str, Any]) -> str:
        run = doc["run"]
        lines = [
            f"{run['strategy']}  {run['first_trading_day']} .. {run['window_end'][:10]}  "
            f"({run['timeframe']}, {len(run['instruments'])} instrument(s))",
            f"run {run['run_id']}  config {run['config_hash'][:19]}",
            "",
            "ASSUMPTIONS",
            *[f"  - {note}" for note in doc["assumptions"]],
            "",
            "RESULT",
            *self._result(doc["metrics"]),
            "",
            "BREAKDOWNS",
            *self._breakdowns(doc["metrics"]["breakdowns"]),
            "",
            "MONTHLY RETURNS",
            *[
                f"  {m['month']}  {self.percent(m['return']):>8}  {m['pnl']:>12}"
                for m in doc["metrics"]["monthly_returns"]
            ],
            "",
            self._activity(doc["activity"]),
        ]
        if run["strategy_halted"]:
            lines += ["", f"STRATEGY HALTED: {run['halt_reason']}"]
        return "\n".join(lines)

    def _result(self, metrics: dict[str, Any]) -> list[str]:
        account, returns = metrics["account"], metrics["returns"]
        trades, drawdown = metrics["trades"], metrics["drawdown"]
        exposure, turnover = metrics["exposure"], metrics["turnover"]
        return [
            f"  start {account['starting_cash']}  end {account['ending_equity']}  "
            f"over {account['trading_days']} trading days",
            f"  total return {self.percent(returns['total_return'])}  "
            f"CAGR {self.percent(returns['cagr'])}  "
            f"max drawdown {self.percent(drawdown['max_drawdown'])}",
            f"  Sharpe {self.plain(returns['sharpe'])}  Sortino {self.plain(returns['sortino'])}  "
            f"Calmar {self.plain(returns['calmar'])}",
            f"  trades {trades['count']}  win rate {self.percent(trades['win_rate'])}  "
            f"profit factor {self.plain(trades['profit_factor'])}  "
            f"expectancy {self.percent(trades['expectancy'])} per trade",
            f"  net P&L {trades['net_pnl']}  "
            f"(gross {trades['gross_pnl']}, charges {trades['fees']})",
            f"  average trade {trades['average_trade']}  "
            f"longest streaks {trades['max_consecutive_wins']} wins / "
            f"{trades['max_consecutive_losses']} losses",
            f"  time in market {self.percent(exposure['time_in_market'])}  "
            f"turnover {self.plain(turnover['annualised_turnover'])}x a year",
        ]

    def _breakdowns(self, breakdowns: dict[str, Any]) -> list[str]:
        sections = [
            ("by direction", breakdowns["by_direction"]),
            ("by regime", breakdowns["by_regime"]),
            ("by time of day", breakdowns["by_time_of_day"]),
            ("by instrument", breakdowns["by_instrument"]),
        ]
        lines: list[str] = []
        for title, groups in sections:
            lines.append(f"  {title}")
            if not groups:
                lines.append("    (no trades)")
                continue
            for name in sorted(groups):
                stats = groups[name]
                lines.append(
                    f"    {name:<16} count {stats['count']:>4}  "
                    f"net {stats['net_pnl']:>12}  win rate {self.percent(stats['win_rate']):>7}"
                )
        return lines

    @staticmethod
    def _activity(activity: dict[str, Any]) -> str:
        return (
            f"ACTIVITY  {activity['signals']} signals, {activity['orders_sent']} orders, "
            f"{activity['fills']} fills, {activity['forced_square_offs']} forced square-offs, "
            f"{activity['orders_cancelled_or_expired']} orders cancelled or expired"
        )

    @staticmethod
    def percent(fraction: str | None) -> str:
        if fraction is None:
            return "n/a"
        return f"{(Decimal(fraction) * 100).quantize(_HUNDREDTH, rounding=ROUND_HALF_EVEN)}%"

    @staticmethod
    def plain(value: str | None) -> str:
        if value is None:
            return "n/a"
        return str(Decimal(value).quantize(_HUNDREDTH, rounding=ROUND_HALF_EVEN))

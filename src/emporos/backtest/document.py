"""`BacktestDocument` — a whole backtest result as plain, exactly-rounded JSON data.

This is what the golden files hold and what a person reads: the run identity, every assumption the
run made, the metrics, what the simulated exchange did, and every closed trade. Numbers are
strings rounded by `MetricsDocument`'s rules; nothing is a float.
"""

from __future__ import annotations

from typing import Any

from emporos.backtest.engine import BacktestResult
from emporos.backtest.metrics.document import MetricsDocument
from emporos.backtest.portfolio import ClosedTrade
from emporos.core.clock import IST

_RISK_NOT_BUILT = (
    "NO RISK ENGINE: every signal was traded as the strategy sized it (Phase 11 is not built); "
    "position limits, loss limits and exposure caps were not applied"
)
_NO_MARGIN = "no margin or funds check: the account is never short of cash"
_PRICING = (
    "orders are priced by a minimal marketable-limit stand-in (limit_buffer_bps from the config, "
    "tick-rounded); Phase 12 owns the real execution pricing"
)
_SQUARE_OFF = (
    "intraday: resting orders are cancelled and positions exited from session.square_off_at; "
    "anything still open at the session's close is closed by the broker's forced square-off"
)


class BacktestDocument:
    def __init__(self, metrics: MetricsDocument | None = None) -> None:
        self._metrics = metrics or MetricsDocument()

    def render(self, result: BacktestResult) -> dict[str, Any]:
        spec = result.spec
        return {
            "run": {
                "run_id": result.run_id,
                "strategy": spec.config.name,
                "config_hash": result.config_hash,
                "timeframe": spec.config.timeframe.value,
                "instruments": [
                    {"symbol": m.symbol, "instrument_id": m.instrument_id}
                    for m in spec.config.universe
                ],
                "window_start": spec.window.start.isoformat(),
                "window_end": spec.window.end.isoformat(),
                "first_trading_day": spec.window.start.astimezone(IST).date().isoformat(),
                "starting_cash": self._metrics.money(spec.starting_cash),
                "strategy_halted": result.runner.halted,
                "halt_reason": result.runner.halt_reason,
                "alerts": [list(alert) for alert in result.alerts],
            },
            "assumptions": self._assumptions(result),
            "activity": {
                "signals": result.counters.signals,
                "signals_refused_by_the_gate": result.counters.gate_rejections,
                "orders_sent": result.counters.orders,
                "orders_rejected_by_the_exchange": result.counters.exchange_rejections,
                "fills": result.counters.fills,
                "orders_cancelled_or_expired": result.counters.cancelled_or_expired,
                "signals_refused_after_square_off": result.counters.refused_after_square_off,
                "square_off_exit_signals": result.counters.square_off_signals,
                "forced_square_offs": result.counters.forced_square_offs,
                "unclosed_bars_skipped": result.runner.unclosed_bars_skipped,
                "positions_open_at_end": result.open_positions_at_end,
            },
            "costs": {
                "schedules": [
                    {"name": name, "effective_from": day.isoformat(), "verified": verified}
                    for name, day, verified in result.costs.schedules
                ],
                "all_verified": result.costs.all_verified,
                "days_priced_with_a_schedule_that_did_not_yet_exist": result.costs.assumed_days,
            },
            "metrics": self._metrics.render(result.metrics),
            "trades": [self._trade(t) for t in result.trades],
        }

    def _assumptions(self, result: BacktestResult) -> list[str]:
        notes = [
            f"risk gate: {result.risk_gate}. {_RISK_NOT_BUILT}"
            if result.risk_gate == "none"
            else f"risk gate: {result.risk_gate}",
            _NO_MARGIN,
            _PRICING,
            _SQUARE_OFF,
            result.spec.fills.describe(),
        ]
        if not result.costs.all_verified:
            notes.append("fee schedule NOT reconciled against a contract note (verified: false)")
        if result.costs.assumed_days:
            notes.append(
                f"{result.costs.assumed_days} trading day(s) predate the oldest fee schedule and "
                "were priced with it"
            )
        return notes + list(result.spec.assumptions)

    def _trade(self, trade: ClosedTrade) -> dict[str, Any]:
        return {
            "instrument_id": trade.instrument_id,
            "direction": trade.direction.value,
            "quantity": trade.quantity,
            "opened_at": trade.opened_at.isoformat(),
            "closed_at": trade.closed_at.isoformat(),
            "entry_price": self._metrics.price(trade.entry_price),
            "exit_price": self._metrics.price(trade.exit_price),
            "gross_pnl": self._metrics.money(trade.gross_pnl),
            "fees": self._metrics.money(trade.fees),
            "net_pnl": self._metrics.money(trade.net_pnl),
        }

"""Names of every collection in plan.md §6 — one place, so no call site uses a string literal."""

from __future__ import annotations

from enum import StrEnum


class Collection(StrEnum):
    USERS = "users"
    ACCOUNTS = "accounts"
    INSTRUMENTS = "instruments"
    INSTRUMENT_VERSIONS = "instrument_versions"
    CANDLES = "candles"
    TICKS = "ticks"
    STRATEGIES = "strategies"
    STRATEGY_RUNS = "strategy_runs"
    SIGNALS = "signals"
    ORDERS = "orders"
    ORDER_EVENTS = "order_events"
    EXECUTIONS = "executions"
    POSITIONS = "positions"
    PORTFOLIO_SNAPSHOTS = "portfolio_snapshots"
    RISK_EVENTS = "risk_events"
    RECONCILIATION_RUNS = "reconciliation_runs"
    BACKTEST_RUNS = "backtest_runs"
    BACKTEST_TRADES = "backtest_trades"
    SYSTEM_EVENTS = "system_events"
    MARKET_CALENDAR = "market_calendar"
    HISTORY_COVERAGE = "history_coverage"
    KILL_SWITCH = "kill_switch"
    COMMANDS = "commands"
    COMMAND_RESULTS = "command_results"

"""Names of every collection in plan.md §6 — one place, so no call site uses a string literal."""

from __future__ import annotations

from enum import StrEnum


class Collection(StrEnum):
    USERS = "users"
    ACCOUNTS = "accounts"
    INSTRUMENTS = "instruments"
    INSTRUMENT_VERSIONS = "instrument_versions"
    CANDLES = "candles"
    STRATEGIES = "strategies"
    STRATEGY_RUNS = "strategy_runs"
    STRATEGY_VERDICTS = "strategy_verdicts"
    SIGNALS = "signals"
    ORDERS = "orders"
    ORDER_EVENTS = "order_events"
    EXECUTIONS = "executions"
    POSITIONS = "positions"
    PORTFOLIO_SNAPSHOTS = "portfolio_snapshots"
    # The paper broker's own books: what a simulated exchange knows. Platform order state
    # (`orders`, `order_events`, ...) is written only by the execution layer, from what the
    # broker reports, exactly as with a real broker — so there is one writer per collection.
    PAPER_ORDERS = "paper_orders"
    PAPER_ORDER_EVENTS = "paper_order_events"
    PAPER_EXECUTIONS = "paper_executions"
    PAPER_POSITIONS = "paper_positions"
    PAPER_PORTFOLIO_SNAPSHOTS = "paper_portfolio_snapshots"
    RISK_EVENTS = "risk_events"
    OPPORTUNITY_SCANS = "opportunity_scans"
    RECONCILIATION_RUNS = "reconciliation_runs"
    BACKTEST_RUNS = "backtest_runs"
    BACKTEST_TRADES = "backtest_trades"
    TRIAL_LEDGER = "trial_ledger"
    FEATURE_TRIAL_LEDGER = "feature_trial_ledger"
    HYPOTHESIS_REGISTRY = "hypothesis_registry"
    CROSS_SECTIONAL_TRIAL_LEDGER = "cross_sectional_trial_ledger"
    LEAD_LAG_TRIAL_LEDGER = "lead_lag_trial_ledger"
    PARITY_REPORTS = "parity_reports"
    GRADUATION_EVENTS = "graduation_events"
    LIVE_ACKNOWLEDGEMENTS = "live_acknowledgements"
    JEV_DECISIONS = "jev_decisions"
    SYSTEM_EVENTS = "system_events"
    MARKET_CALENDAR = "market_calendar"
    CORPORATE_ACTION_QUARANTINE = "corporate_action_quarantine"
    HISTORY_COVERAGE = "history_coverage"
    KILL_SWITCH = "kill_switch"
    COMMANDS = "commands"
    COMMAND_RESULTS = "command_results"

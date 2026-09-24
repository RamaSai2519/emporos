"""The vocabulary of a research experiment report: what may be said about why a result was judged
the way it was. Pure data; how a report is built, stored and rendered lives in the layers above."""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """A machine-readable reason behind a gate finding, one per gate.

    The human `detail` of a finding says what was measured; the code says which rule spoke, so
    reports across every strategy family can be grouped and compared by reason, not by prose.
    """

    NET_PNL_NOT_REAL = "net_pnl_not_real"
    ADVERSE_COSTS = "adverse_costs"
    TOO_FEW_WINDOWS_PROFITABLE = "too_few_windows_profitable"
    TOO_FEW_TRADES = "too_few_trades"
    TOO_LITTLE_HISTORY = "too_little_history"
    DRAWDOWN_OVER_BUDGET = "drawdown_over_budget"
    PROFIT_CONCENTRATED = "profit_concentrated"
    PARAMETER_UNSTABLE = "parameter_unstable"
    DSR_BELOW_THRESHOLD = "dsr_below_threshold"
    PBO_ABOVE_THRESHOLD = "pbo_above_threshold"
    EDGE_BELOW_COST_ERROR = "edge_below_cost_error"
    BELOW_BASELINE = "below_baseline"
    TOO_FEW_REGIMES = "too_few_regimes"
    HOLDOUT_NOT_RESERVED = "holdout_not_reserved"
    HOLDOUT_NOT_EVALUATED = "holdout_not_evaluated"

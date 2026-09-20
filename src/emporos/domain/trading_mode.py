"""Whether an order is real money or simulated (plan.md §11 `TradingModeGuard`)."""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"

"""Order vocabulary shared by every layer.

`OrderType` deliberately has no `MARKET` or `IOC` member: a forbidden order type
must be *unrepresentable*, not merely validated against (Decision 8).
"""

from __future__ import annotations

from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    STOPLOSS_LIMIT = "STOPLOSS_LIMIT"

"""Fault injection for the placement path: the lost reply (Decision 7).

The real failure that makes duplicate orders possible is an order that WAS accepted whose reply
never arrived. `ReplyLoss` policies reproduce it: the order is created, journaled and live, and
the caller nonetheless gets an ambiguous error and must resolve it with `find_orders_by_tag`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from emporos.broker.models import PlaceOrderRequest
from emporos.broker.paper.chance import Chance


class ReplyLoss(Protocol):
    def loses_reply(self, request: PlaceOrderRequest) -> bool: ...


class ReliableReplies:
    """Every reply arrives: the default."""

    def loses_reply(self, request: PlaceOrderRequest) -> bool:
        return False


class RandomReplyLoss:
    """Loses a configurable share of replies at random."""

    def __init__(self, rate: Decimal, chance: Chance) -> None:
        if not Decimal(0) <= rate <= Decimal(1):
            raise ValueError("a loss rate lies between 0 and 1")
        self._rate = rate
        self._chance = chance

    def loses_reply(self, request: PlaceOrderRequest) -> bool:
        return self._chance.hits(self._rate)


class ScriptedReplyLoss:
    """Loses exactly the reply to the next order after `lose_next()` is called."""

    def __init__(self) -> None:
        self._armed = False

    def lose_next(self) -> None:
        self._armed = True

    def loses_reply(self, request: PlaceOrderRequest) -> bool:
        armed, self._armed = self._armed, False
        return armed

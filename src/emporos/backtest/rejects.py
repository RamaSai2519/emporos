"""Simulated order rejection (plan.md §10: rejected orders at a configurable rate)."""

from __future__ import annotations

import random
from typing import Protocol

from emporos.backtest.orders import SimOrderRequest

_BASIS = 10_000


class RejectPolicy(Protocol):
    def rejection(self, request: SimOrderRequest) -> str | None:
        """Why the exchange refuses this order, or None to accept it."""
        ...


class NeverReject:
    def rejection(self, request: SimOrderRequest) -> str | None:
        return None


class SeededRejectRate:
    """Refuses a fixed share of orders, chosen by a seeded generator: the same seed and the same
    orders reject the same ones, on any machine. The rate is in basis points (100 = 1%)."""

    def __init__(self, rate_bps: int, seed: int) -> None:
        if not 0 <= rate_bps <= _BASIS:
            raise ValueError("the rejection rate is 0..10000 basis points")
        self._rate_bps = rate_bps
        self._rng = random.Random(seed)

    def rejection(self, request: SimOrderRequest) -> str | None:
        drawn = self._rng.randrange(
            _BASIS
        )  # drawn for EVERY order, so one decision never shifts another
        return "simulated exchange rejection" if drawn < self._rate_bps else None

"""The one interface every risk rule implements, and the arithmetic several rules share."""

from __future__ import annotations

from typing import Protocol

from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.risk.snapshot import RiskSnapshot
from emporos.risk.verdict import RuleVerdict


class RiskRule(Protocol):
    """A rule is a pure function of the signal and the snapshot: no I/O, no clock, no state."""

    name: str

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict: ...


def is_entry(signal: Signal) -> bool:
    return signal.kind is SignalKind.ENTRY


def signed_quantity(side: OrderSide, quantity: int) -> int:
    return quantity if side is OrderSide.BUY else -quantity


def net_after(signal: Signal, snapshot: RiskSnapshot) -> tuple[int, int]:
    """(net quantity now, net quantity if the whole signal fills)."""
    now = snapshot.account.position(signal.instrument_id).net_quantity
    return now, now + signed_quantity(signal.side, signal.quantity)


def added_exposure(signal: Signal, snapshot: RiskSnapshot) -> int:
    """Shares of extra exposure the signal creates; zero when it only reduces the position."""
    now, after = net_after(signal, snapshot)
    return max(abs(after) - abs(now), 0)

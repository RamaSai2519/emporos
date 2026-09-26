"""Which index option an E2 signal buys (EM-248, declaration `s1-size-target-trail`, amendment A).

The ATM contract of the nearest WEEKLY expiry with at least one full session left (an expiry after
the signal's day: on its own day less than a session is left); an index with no weekly listed that
day (BANKNIFTY after November 2024) takes the nearest MONTHLY that qualifies. CE for an up move, PE
for a down move. The listed expiries and the lot sizes come behind small protocols (the F&O archive
supplies them); the report counts trades by expiry kind."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from emporos.research.iv.black76 import Right

__all__ = [
    "STRIKE_STEPS", "Contract", "ContractPicker", "ExpiryCalendar", "ExpiryKind", "ListedExpiry",
    "LotSizes",
]  # fmt: skip

STRIKE_STEPS: Mapping[str, float] = {"NIFTY": 50.0, "BANKNIFTY": 100.0}


class ExpiryKind(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class ListedExpiry:
    expiry: date
    kind: ExpiryKind


@dataclass(frozen=True)
class Contract:
    underlying: str
    expiry: date
    kind: ExpiryKind
    strike: float
    right: Right
    lot: int  # units per lot, as of the day


class ExpiryCalendar(Protocol):
    def listed(self, underlying: str, day: date) -> Sequence[ListedExpiry]:
        """The expiries with options listed for the underlying on `day`."""
        ...


class LotSizes(Protocol):
    def lot(self, underlying: str, day: date) -> int | None: ...


class ContractPicker:
    def __init__(
        self,
        calendar: ExpiryCalendar,
        lots: LotSizes,
        steps: Mapping[str, float] = STRIKE_STEPS,
    ) -> None:
        self._calendar, self._lots, self._steps = calendar, lots, dict(steps)

    def pick(self, underlying: str, day: date, spot: float, direction: int) -> Contract | str:
        """The contract, or the reason there is none: `no_step`, `no_expiry`, `no_lot`."""
        step = self._steps.get(underlying)
        if step is None:
            return "no_step"
        expiry = self._expiry(underlying, day)
        lot = self._lots.lot(underlying, day)
        if expiry is None:
            return "no_expiry"
        if lot is None:
            return "no_lot"
        strike = round(spot / step) * step
        right = Right.CALL if direction > 0 else Right.PUT
        return Contract(underlying, expiry.expiry, expiry.kind, strike, right, lot)

    def _expiry(self, underlying: str, day: date) -> ListedExpiry | None:
        open_ones = sorted(
            (e for e in self._calendar.listed(underlying, day) if e.expiry > day),
            key=lambda e: e.expiry,
        )
        for kind in (ExpiryKind.WEEKLY, ExpiryKind.MONTHLY):
            found = [e for e in open_ones if e.kind is kind]
            if found:
                return found[0]
        return None

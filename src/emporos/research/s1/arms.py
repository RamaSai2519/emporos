"""The 36 cash arms and 18 option arms of the declaration, and how each builds its exit policy."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from emporos.research.s1.exit_policies import (
    AtrStop,
    AtrTrail,
    ExitPolicy,
    HalfPeakTrail,
    HalfTargetStop,
    LockFixedTrail,
    OneToOneStop,
    StopRule,
    TargetThenTrail,
    TrailRule,
)

__all__ = ["Arm", "cash_arms", "option_arms"]

STOPS = ("one_to_one", "half", "atr")
TRAILS = ("lock_fixed", "half_peak", "atr")
LEVERAGES = (1, 5)
TARGET_PS = (0.005, 0.010)
LOCK_GAP_STOCK = 0.003  # the fixed trailing gap for stocks
LOCK_GAP_OPTION = 0.05  # of the premium


@dataclass(frozen=True)
class Arm:
    book: str  # "cash" or "options"
    stop: str
    leverage: int
    target_p: float
    trail: str

    @property
    def name(self) -> str:
        lev = f"x{self.leverage}" if self.book == "cash" else ""
        return f"{self.book}:{self.stop}:{lev}:{self.target_p:.3f}:{self.trail}".replace("::", ":")

    def stop_rule(self) -> StopRule:
        rules: dict[str, StopRule] = {
            "one_to_one": OneToOneStop(), "half": HalfTargetStop(), "atr": AtrStop(),
        }  # fmt: skip
        return rules[self.stop]

    def trail_rule(self) -> TrailRule:
        gap = LOCK_GAP_STOCK if self.book == "cash" else LOCK_GAP_OPTION
        rules: dict[str, TrailRule] = {
            "lock_fixed": LockFixedTrail(gap), "half_peak": HalfPeakTrail(), "atr": AtrTrail(),
        }  # fmt: skip
        return rules[self.trail]

    def policy(self, cost_fraction: float) -> ExitPolicy:
        """The target is the round-trip cost (a fraction of the notional) plus `target_p`."""
        return TargetThenTrail(cost_fraction + self.target_p, self.stop_rule(), self.trail_rule())


def cash_arms() -> list[Arm]:
    return [
        Arm("cash", s, lev, p, t) for s, lev, p, t in product(STOPS, LEVERAGES, TARGET_PS, TRAILS)
    ]


def option_arms() -> list[Arm]:
    return [Arm("options", s, 1, p, t) for s, p, t in product(STOPS, TARGET_PS, TRAILS)]

"""The MODELLED premium of an index option, bar by bar (EM-248, declaration `s1-size-target-trail`).

Black-76 on the index spot of each 5-minute bar, with the previous session's futures basis, the
ATM contract's implied volatility from the previous session's settle price held constant through
the day, and the time to expiry in minutes (to 15:30 IST on the expiry day, nights and weekends
included). Every number is an ASSUMPTION of a model, labelled as such in every report; where the EC2
recorder's quotes exist, `model_error` measures the model against them.

A call's premium rises with the spot and a put's falls, so a bar's high and low premium are the
model at the spot's high and low (swapped for a put): the premium bars keep the spot bars' shape and
feed the same exit policies as a stock's. A modelled bar has no volume, so its volume is unbounded
(the 10% participation rule cannot bind on a model)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np

from emporos.research.iv.black76 import Right, black76_price
from emporos.research.s1.bars import DayBars
from emporos.research.s1.option_contracts import Contract

__all__ = ["ModelInputs", "PremiumModel", "PriorSession", "UNBOUNDED_VOLUME"]

EXPIRY_MINUTE = 15 * 60 + 30
MINUTES_PER_YEAR = 365.0 * 24 * 60
UNBOUNDED_VOLUME = 1e12


@dataclass(frozen=True)
class ModelInputs:
    """What the PREVIOUS session settled at, for one contract's expiry."""

    spot: float  # the underlying's close
    forward: float  # the forward (the future, or the put-call parity forward) at that settle
    atm_iv: float  # the ATM implied volatility
    rate: float  # the repo rate as of the day

    def __post_init__(self) -> None:
        if min(self.spot, self.forward, self.atm_iv) <= 0:
            raise ValueError("spot, forward and volatility must be positive")


class PriorSession(Protocol):
    def inputs(self, contract: Contract, day: date) -> ModelInputs | None:
        """The settle of the session BEFORE `day` (never `day`'s own), or None when it has none."""
        ...


class PremiumModel:
    def __init__(self, contract: Contract, inputs: ModelInputs) -> None:
        self._contract, self._inputs = contract, inputs
        self._basis = inputs.forward / inputs.spot

    @property
    def contract(self) -> Contract:
        return self._contract

    def years(self, day: date, minute: int) -> float:
        minutes = (self._contract.expiry - day).days * 24 * 60 + EXPIRY_MINUTE - minute
        return max(minutes, 0) / MINUTES_PER_YEAR

    def premium(self, spot: float, day: date, minute: int) -> float:
        c, i = self._contract, self._inputs
        return black76_price(
            spot * self._basis, c.strike, self.years(day, minute), i.rate, i.atm_iv, c.right
        )

    def bars(self, spot: DayBars, day: date) -> DayBars:
        """Premium bars for the contract on `day`, from that day's spot bars."""
        up = self._contract.right is Right.CALL
        n = len(spot)
        out = {name: np.zeros(n) for name in ("open", "high", "low", "close")}
        for i in range(n):
            minute = int(spot.closes_at[i])
            o, c = (self.premium(float(x[i]), day, minute) for x in (spot.open, spot.close))
            at_high, at_low = (
                self.premium(float(x[i]), day, minute) for x in (spot.high, spot.low)
            )
            high, low = (at_high, at_low) if up else (at_low, at_high)
            out["open"][i], out["close"][i] = o, c
            out["high"][i], out["low"][i] = max(high, o, c), min(low, o, c)
        return DayBars(
            spot.closes_at, out["open"], out["high"], out["low"], out["close"],
            np.full(n, UNBOUNDED_VOLUME),
        )  # fmt: skip

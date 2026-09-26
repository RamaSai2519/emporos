"""The previous session's settle as the option model's inputs, from the daily IV dataset (EM-244).

`IvRow` (one row per underlying, day and expiry, from that day's own settle prices) supplies the
forward, the ATM implied volatility and the repo rate; the underlying's close of the same session
supplies the spot, so the basis is `forward / spot`. Only the session BEFORE the trade day is ever
read, and a row whose day is not that session's spot day is refused rather than mixed."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from emporos.research.iv.dataset import IvRow
from emporos.research.s1.option_contracts import Contract
from emporos.research.s1.option_model import ModelInputs

__all__ = ["IvPriorSession", "IvRowsBefore", "SpotCloseBefore"]


class IvRowsBefore(Protocol):
    def latest_before(self, symbol: str, day: date) -> Sequence[IvRow]:
        """The rows of the latest day strictly before `day` on which the symbol has any."""
        ...


class SpotCloseBefore(Protocol):
    def close_before(self, symbol: str, day: date) -> tuple[date, float] | None:
        """(the session's date, the underlying's close) for the latest session before `day`."""
        ...


class IvPriorSession:
    def __init__(self, rows: IvRowsBefore, spots: SpotCloseBefore) -> None:
        self._rows, self._spots = rows, spots

    def inputs(self, contract: Contract, day: date) -> ModelInputs | None:
        found = self._spots.close_before(contract.underlying, day)
        if found is None:
            return None
        session, spot = found
        for row in self._rows.latest_before(contract.underlying, day):
            if (
                row.day == session
                and row.expiry == contract.expiry
                and row.atm_iv is not None
                and row.atm_iv > 0
            ):
                return ModelInputs(spot, row.forward, row.atm_iv, row.rate)
        return None

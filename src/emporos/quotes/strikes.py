"""Which contracts to record right now: a window of strikes around the money (EM-246).

The at-the-money strike is the listed strike nearest the underlying's last price; the window is that
strike and `each_side` listed strikes either way, calls and puts, in each of the nearest `expiries`
expiries. The set is rebuilt from a fresh spot every fifteen minutes, so it follows the market."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.quotes.contract import ContractBook, OptionContract

__all__ = ["StrikePlanner", "UnderlyingRule"]


@dataclass(frozen=True)
class UnderlyingRule:
    underlying: str  # the master's name
    spot_id: str  # the id whose quote is the spot: "NSE:99926000", "NSE:2885"
    expiries: int  # how many of the nearest expiries
    each_side: int  # listed strikes either side of the money

    def __post_init__(self) -> None:
        if self.expiries < 1 or self.each_side < 0:
            raise ValueError("record at least one expiry and no negative strike window")


class StrikePlanner:
    def __init__(self, book: ContractBook) -> None:
        self._book = book

    def plan(self, rule: UnderlyingRule, spot: Decimal, today: date) -> list[OptionContract]:
        chosen: list[OptionContract] = []
        for expiry in self._book.expiries(rule.underlying, today)[: rule.expiries]:
            strikes = self._book.strikes(rule.underlying, expiry)
            if not strikes:
                continue
            money = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
            for i in range(
                max(0, money - rule.each_side), min(len(strikes), money + rule.each_side + 1)
            ):
                for right in ("CE", "PE"):
                    contract = self._book.get(rule.underlying, expiry, strikes[i], right)
                    if contract is not None:
                        chosen.append(contract)
        return chosen

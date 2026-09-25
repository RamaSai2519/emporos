"""What a swing strategy is, and the small rules around it (EM-223).

A `SwingStrategy` is asked once per session, after the close, what it wants to hold. It sees the
dataset only up to that session, the holdings it already has (with how long), and nothing else. It
answers with an ordered list of `Intent`s; the simulator sells what is no longer wanted, buys what
is new, in that order, at the NEXT session's open, and does not resize what it keeps.

Sizing is per intent: `size_multiple` scales the base slot (equity / max positions). 1 is the base;
a declared conviction rule may return 1.5 or 2 for the names where its pre-declared test holds. The
simulator never lets the multiple exceed the config's ceiling and never spends cash it does not
have, so this is not leverage.

Wrappers compose a strategy with a regime filter (`RegimeGated`) without either knowing the other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from emporos.research.swing.data import AsOfView

__all__ = [
    "AllMembers", "DecisionContext", "Holding", "Intent", "Membership", "RegimeFilter",
    "RegimeGated", "SwingStrategy",
]  # fmt: skip


@dataclass(frozen=True)
class Intent:
    instrument_id: str
    size_multiple: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if self.size_multiple <= 0:
            raise ValueError("a size multiple is positive")


@dataclass(frozen=True)
class Holding:
    instrument_id: str
    entry_index: int  # calendar index of the fill
    entry_day: date
    sessions_held: int  # calendar sessions from the fill to the decision, 0 on the fill day


@dataclass(frozen=True)
class DecisionContext:
    day: date
    index: int  # calendar index of the decision session
    view: AsOfView
    holdings: Mapping[str, Holding]
    equity: Decimal
    tradable: frozenset[str]  # names with a bar today that are members today


class SwingStrategy(Protocol):
    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        """The names to hold from the next open, most wanted first. Names beyond the config's
        `max_positions` are ignored. A name held and still listed is kept as it is."""
        ...


class Membership(Protocol):
    def is_member(self, instrument_id: str, day: date) -> bool: ...


class AllMembers:
    """Every name is always a member: today's constituents projected back in time, which flatters
    a long-only rule by survivorship. Fine for machinery; a Track A result that reaches S4 must use
    as-of membership (PROFIT_PLAN §2.5)."""

    def is_member(self, instrument_id: str, day: date) -> bool:
        return True


class RegimeFilter(Protocol):
    def is_on(self, context: DecisionContext) -> bool: ...


class RegimeGated:
    """Wants nothing (so: cash) whenever the filter is off; otherwise defers to the strategy."""

    def __init__(self, strategy: SwingStrategy, regime: RegimeFilter) -> None:
        self._strategy = strategy
        self._regime = regime

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        return self._strategy.desired(context) if self._regime.is_on(context) else ()

"""The position value a piece of research is judged at (EM-191 F4, EDGE_SEARCH_PLAN.md §4.1).

Cost is a function of size: at Rs 5,000 a round trip costs 0.371% of the position, at Rs 25,000
0.324% (plan §1.1). A result is therefore only meaningful together with the size it was measured
at, and that size must be fixed BEFORE the run, not picked afterwards to flatter it. `DeclaredSize`
is that number, with where it came from; every study and curation takes one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from emporos.domain.money import Money

__all__ = ["DeclaredSize", "SizeResolver", "SizeSource"]


class SizeSource(StrEnum):
    DECLARED = "declared"  # stated in the experiment's declaration, or on the command line
    RISK_DEFAULT = "risk_default"  # nothing stated: the risk limits' own max_position_value


@dataclass(frozen=True)
class DeclaredSize:
    position_value: Decimal  # rupees per position
    source: SizeSource = SizeSource.DECLARED

    def __post_init__(self) -> None:
        if self.position_value <= 0:
            raise ValueError("the declared position value must be positive")

    def quantity_at(self, price: Money) -> int:
        """Whole shares that fit the declared value at `price`, rounded down like the risk engine
        does. 0 means the trade cannot be taken (one share costs more than the declared value)."""
        if price.amount <= 0:
            return 0
        return int(self.position_value // price.amount)

    def exceeds(self, limit: Decimal) -> bool:
        """True when this size is above `limit`: research at that size needs the limit raised."""
        return self.position_value > limit


class SizeResolver:
    """Turns what was stated (a declaration, a command-line override) into the one size used.

    The declaration is the pre-registered claim, so an override that disagrees with it is refused
    rather than allowed to quietly change what was declared."""

    def __init__(self, default_value: Decimal) -> None:
        self._default = DeclaredSize(default_value, SizeSource.RISK_DEFAULT)

    def resolve(
        self, *, declared: Decimal | None = None, override: Decimal | None = None
    ) -> DeclaredSize:
        if declared is not None and override is not None and declared != override:
            raise ValueError(
                f"the declaration fixes the position value at {declared}; "
                f"{override} was given on the command line. Declare a new experiment to change it"
            )
        stated = declared if declared is not None else override
        return self._default if stated is None else DeclaredSize(stated, SizeSource.DECLARED)

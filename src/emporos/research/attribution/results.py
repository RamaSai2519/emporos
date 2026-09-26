"""What the three atlas calls return, as frozen values read strictly from the model's JSON
(EM-245)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from emporos.research.attribution.taxonomy import Driver

__all__ = ["Attribution", "CrossingCall", "DirectionRead", "Outlook", "Way"]

NO_ITEM = "none"


class Outlook(StrEnum):
    CONTINUE = "continue"
    FIZZLE = "fizzle"
    UNCLEAR = "unclear"


class Way(StrEnum):
    UP = "up"
    DOWN = "down"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Attribution:
    """Call A: the primary driver of a move already seen (hindsight, descriptive only)."""

    primary: Driver
    secondary: Driver | None
    confidence: int  # 0..100
    cited_item: str | None  # an id the code then checks exists
    reason: str

    @property
    def unexplained(self) -> bool:
        return self.primary is Driver.U


@dataclass(frozen=True)
class CrossingCall:
    """Call C: the cause at a crossing, from what was public then."""

    driver: Driver
    outlook: Outlook
    confidence: int
    horizon_minutes: int  # the expected horizon of the continuation, 0 when unclear
    cited_item: str | None
    reason: str


@dataclass(frozen=True)
class DirectionRead:
    """Call B: which way a cause points, blind to the price move."""

    way: Way
    horizon_minutes: int
    confidence: int
    reason: str

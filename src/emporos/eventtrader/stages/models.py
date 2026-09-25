"""What each pipeline stage returns, as frozen values validated from the model's JSON (EM-240)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ArbiterResult",
    "Horizon",
    "Instrument",
    "JudgeResult",
    "PanelView",
    "Posture",
    "PostureResult",
    "Side",
    "TriageDirection",
    "TriageResult",
]


class TriageDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    NONE = "none"


class Horizon(StrEnum):
    INTRADAY = "intraday"
    SWING = "swing"
    NONE = "none"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class Instrument(StrEnum):
    CASH_INTRADAY = "cash_intraday"
    CASH_SWING = "cash_swing"
    CALL = "call"
    PUT = "put"


class Posture(StrEnum):
    HOLD = "hold"
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class TriageResult:
    material: bool
    direction: TriageDirection
    expected_move_pct: float  # 0..50, the size of the move the model expects
    horizon: Horizon
    priced_in: bool  # the reaction so far already reflects it
    confidence: int  # 0..100
    reason: str


@dataclass(frozen=True)
class PanelView:
    persona: str  # "bull", "bear" or "tape"
    side: Side | None  # None = no trade
    conviction: int  # 0..100
    instrument: Instrument | None
    reason: str


@dataclass(frozen=True)
class JudgeResult:
    trade: bool
    instrument: Instrument | None
    side: Side | None
    stop_pct: float  # distance to the stop from entry, percent of entry (0 when no trade)
    target_pct: float
    hold_days: int  # 0 = intraday
    confidence: int
    reason: str


@dataclass(frozen=True)
class ArbiterResult:
    approve: bool
    confidence: int
    reason: str


@dataclass(frozen=True)
class PostureResult:
    posture: Posture
    reason: str

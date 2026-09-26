"""One move, as the ledger records it (EM-243)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

__all__ = ["FORWARD_SESSIONS", "EventClass", "MoveEvent", "OnsetKind"]

FORWARD_SESSIONS = (1, 3, 5, 10)


class EventClass(StrEnum):
    STOCK_DAILY = "stock_daily"  # |residual| >= 2.5 sigma over the day
    STOCK_JUMP = "stock_jump"  # a 15-minute residual >= 3 sigma
    SECTOR = "sector"  # a sector index's residual against NIFTY
    MARKET = "index"  # NIFTY itself: the whole-market class
    PLACEBO = "placebo"  # a quiet name-session, drawn with the seed


class OnsetKind(StrEnum):
    OVERNIGHT = "overnight"  # the open gap alone was at least half the move
    INTRADAY = "intraday"  # the first bar at which the day's residual passed a quarter of it
    JUMP = "jump"  # the end of the 15-minute window that jumped
    NONE = "none"  # the day's bars were too incomplete to say


@dataclass(frozen=True)
class MoveEvent:
    event_id: str
    event_class: EventClass
    name: str  # the trading symbol, or the sector index's name, or NIFTY
    instrument_id: str
    sector: str  # the sector index's name ("" when none)
    group: str  # "" when the name is in no group
    day: date
    direction: int  # +1 or -1: the sign of the residual (of the raw return for the market)
    return_pct: float  # the session's raw return
    resid_pct: float  # the session's residual after market, sector and group
    sigma_pct: float  # the scale it is measured against (daily, or 15-minute for a jump)
    z: float
    gap_resid_pct: float  # the overnight gap's residual
    onset_kind: OnsetKind
    onset_at: datetime | None  # IST; 09:15 for an overnight onset
    peak_at: datetime | None  # IST: when the day's residual was furthest in its direction
    after_15m_pct: (
        float  # residual added in the 15 minutes after onset (signed as the market moved)
    )
    after_60m_pct: float
    after_close_pct: float  # to 15:15
    forward_raw_pct: tuple[float, ...]  # from the session close, over FORWARD_SESSIONS
    forward_resid_pct: tuple[float, ...]
    beta_market: float
    beta_sector: float
    beta_group: float

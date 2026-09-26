"""The candidate causes an attribution call may cite, with the time each became public
(driver-atlas-plan §3.2 and §3.3, EM-245)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["CauseItem", "ItemClass", "MAX_ITEM_CHARS"]

MAX_ITEM_CHARS = 600  # a prompt is billed by the token


class ItemClass(StrEnum):
    CALENDAR = "calendar"  # macro / policy / expiry / index-notice events
    FLOW = "flow"  # FII/DII, participant-wise positions
    BULK_DEAL = "bulk_deal"
    INSIDER = "insider"  # PIT / SAST disclosures
    FILING = "filing"  # exchange announcements
    GROUP = "group"  # a group company's news
    ASIA = "asia"  # Asia closes
    HEADLINE = "headline"


@dataclass(frozen=True)
class CauseItem:
    item_id: str
    item_class: ItemClass
    text: str
    available_at: datetime  # tz-aware: when a trader could first have known it
    symbol: str = ""  # the company it concerns, "" for a market-wide item

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("a cause item needs an id")
        if self.available_at.tzinfo is None:
            raise ValueError("a cause item's availability time must be timezone-aware")

    def render(self, now: datetime) -> dict[str, str | int]:
        """What a prompt shows: the id, the class, how long ago it became public, its text. No
        calendar date."""
        minutes = int((now - self.available_at).total_seconds() // 60)
        return {
            "id": self.item_id,
            "class": self.item_class.value,
            "minutes_since_public": minutes,
            "text": self.text[:MAX_ITEM_CHARS],
        }


def by_id(items: Iterable[CauseItem]) -> dict[str, CauseItem]:
    return {i.item_id: i for i in items}

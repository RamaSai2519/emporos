"""Routine filings dropped before triage, as a FIXED list (EM-240, declared before any label or
model reply was seen): they are the bulk of the feed and carry no tradable news, and every one
sent to the model is money spent on a "not material". The list is by NSE category, matched
exactly.

Every event the filter drops is counted by category and reported; none reaches a prompt, a
decision or the replay."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from emporos.eventtrader.events import MarketEvent

__all__ = ["ROUTINE_CATEGORIES", "CategoryPreFilter"]

ROUTINE_CATEGORIES = frozenset(
    {
        "Loss of Share Certificates",
        "Issue of Duplicate Share Certificate",  # the same paperwork, after the loss
        "Trading Window",
        "Copy of Newspaper Publication",
        "Certificate under SEBI (Depositories and Participants) Regulations, 2018",  # Reg 74(5)
        "Shareholders meeting",  # notices of meetings
        "ESOP/ESOS/ESPS",  # allotments under employee plans
    }
)


class CategoryPreFilter:
    def __init__(self, categories: Iterable[str] = ROUTINE_CATEGORIES) -> None:
        self._categories = frozenset(categories)

    @property
    def categories(self) -> frozenset[str]:
        return self._categories

    def split(self, events: Sequence[MarketEvent]) -> tuple[list[MarketEvent], Counter[str]]:
        """(kept in order, dropped counted by category)."""
        kept: list[MarketEvent] = []
        dropped: Counter[str] = Counter()
        for event in events:
            if event.category in self._categories:
                dropped[event.category] += 1
            else:
                kept.append(event)
        return kept, dropped

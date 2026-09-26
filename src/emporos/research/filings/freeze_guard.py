"""When is the event store fit to freeze for a Dev run? (EM-240, EM-239)

A snapshot is never rebuilt, so freezing an empty or half-read store would poison every variant that
reads it. The guard is deliberately dumb and refuses on three counts: too few events in the store,
too few MATERIAL 2024 attachments in scope (a wiped raw store reads as ZERO, not as "nothing
pending"), and any of them still unread. `reason` is None only when all three hold."""

from __future__ import annotations

from dataclasses import dataclass

from emporos.research.filings.progress import ExtractionProgress

__all__ = ["FreezeGuard", "MIN_EVENTS", "MIN_TIER1"]

MIN_EVENTS = 50_000  # the store held 56,736 when it was whole
MIN_TIER1 = 6_000  # the 2024 MATERIAL attachments in D1 names: 6,509 when whole


@dataclass(frozen=True)
class FreezeGuard:
    min_events: int = MIN_EVENTS
    min_tier1: int = MIN_TIER1

    def reason(self, events: int, tier1: ExtractionProgress) -> str | None:
        """Why the store is NOT ready to freeze, or None when it is."""
        if events < self.min_events:
            return f"the store holds {events:,} events, fewer than {self.min_events:,}"
        if tier1.total < self.min_tier1:
            return (
                f"only {tier1.total:,} material 2024 attachments are in scope, "
                f"fewer than {self.min_tier1:,}"
            )
        if tier1.pending:
            return f"{tier1.pending:,} of {tier1.total:,} material 2024 attachments are unread"
        return None

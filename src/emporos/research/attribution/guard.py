"""The cause-availability guard (driver-atlas-plan §3.3, EM-245): no item that was not yet public
at the decision moment may reach a prompt that trades on it. A test pins it for every call."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from emporos.research.attribution.items import CauseItem

__all__ = [
    "ATTRIBUTION_AFTER", "ATTRIBUTION_BEFORE", "HindsightLeak", "assert_public", "candidates",
    "public_by",
    "post_onset",
]  # fmt: skip

ATTRIBUTION_BEFORE = timedelta(hours=24)
ATTRIBUTION_AFTER = timedelta(minutes=30)


class HindsightLeak(ValueError):
    """An item that was not public at the cutoff was about to enter a prompt."""


def assert_public(items: Iterable[CauseItem], cutoff: datetime) -> None:
    for item in items:
        if item.available_at > cutoff:
            raise HindsightLeak(
                f"item {item.item_id} became public {item.available_at.isoformat()}, "
                f"after the cutoff {cutoff.isoformat()}"
            )


def candidates(
    items: Iterable[CauseItem], onset: datetime, before: timedelta = ATTRIBUTION_BEFORE,
    after: timedelta = ATTRIBUTION_AFTER,
) -> list[CauseItem]:  # fmt: skip
    """Call A's candidates: public in [onset - 24 h, onset + 30 min], oldest first."""
    kept = [i for i in items if onset - before <= i.available_at <= onset + after]
    return sorted(kept, key=lambda i: (i.available_at, i.item_id))


def post_onset(item: CauseItem, onset: datetime) -> bool:
    """Recorded, never hidden: a cause public after the onset can still be the cause (a leak or
    anticipation), but it is flagged, and it could not have been traded."""
    return item.available_at > onset


def public_by(items: Sequence[CauseItem], cutoff: datetime) -> list[CauseItem]:
    return sorted(
        (i for i in items if i.available_at <= cutoff), key=lambda i: (i.available_at, i.item_id)
    )

"""The fixed pre-filter of routine filings."""

from __future__ import annotations

from emporos.eventtrader.prefilter import ROUTINE_CATEGORIES, CategoryPreFilter
from tests.unit.eventtrader.fakes import event


def test_routine_categories_are_dropped_in_order_and_counted_and_the_rest_kept() -> None:
    events = [
        event(event_id="1", category="Outcome of Board Meeting"),
        event(event_id="2", category="Loss of Share Certificates"),
        event(event_id="3", category="Trading Window"),
        event(event_id="4", category="Press Release"),
        event(event_id="5", category="Loss of Share Certificates"),
    ]

    kept, dropped = CategoryPreFilter().split(events)

    assert [e.event_id for e in kept] == ["1", "4"]
    assert dropped == {"Loss of Share Certificates": 2, "Trading Window": 1}


def test_the_list_is_the_declared_one_and_matches_exactly() -> None:
    assert len(ROUTINE_CATEGORIES) == 7
    assert "Copy of Newspaper Publication" in ROUTINE_CATEGORIES
    kept, dropped = CategoryPreFilter().split([event(category="Trading Window Closure")])

    assert len(kept) == 1 and not dropped  # only the exact category

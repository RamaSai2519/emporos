"""EM-240: the event value objects refuse what would leak or mislead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.eventtrader.events import FILING, HEADLINE, MarketEvent

T = datetime(2024, 3, 4, 5, 0, tzinfo=UTC)


def event(**overrides: object) -> MarketEvent:
    values: dict[str, object] = {
        "event_id": "E1", "instrument_id": "NSE:2885", "symbol": "RELIANCE", "published_at": T,
        "usable_from": T, "kind": FILING, "category": "Outcome", "subject": "Results",
        "text": "Revenue up 12%",
    }  # fmt: skip
    return MarketEvent(**{**values, **overrides})  # type: ignore[arg-type]


def test_a_timestamped_event_is_usable_when_published() -> None:
    assert event().usable_from == event().published_at


def test_a_date_only_headline_can_be_usable_later_but_never_earlier() -> None:
    assert event(kind=HEADLINE, usable_from=T + timedelta(hours=18)).usable_from > T
    with pytest.raises(ValueError, match="usable before"):
        event(usable_from=T - timedelta(seconds=1))


def test_times_must_be_timezone_aware_and_the_kind_known() -> None:
    naive = datetime(2024, 3, 4, 5, 0)
    with pytest.raises(ValueError, match="timezone"):
        event(published_at=naive, usable_from=naive)
    with pytest.raises(ValueError, match="kind"):
        event(kind="tweet")
    with pytest.raises(ValueError, match="id and a symbol"):
        event(symbol="")

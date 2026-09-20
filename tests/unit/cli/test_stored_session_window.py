"""A holiday in the stored exchange calendar is not an open session (EM-99 A4)."""

from collections.abc import Mapping
from datetime import UTC, date, datetime

from emporos.cli.worker_composition import stored_session_window


class Store:
    async def load_all(self) -> dict[date, bool]:
        return {date(2026, 1, 26): False, date(2026, 1, 27): True}  # Republic Day is a holiday

    async def save(self, days: Mapping[date, bool]) -> None: ...


async def test_a_holiday_is_closed_a_weekend_is_closed_and_an_unknown_weekday_trades() -> None:
    window = await stored_session_window(Store())
    at = lambda d: datetime(2026, 1, d, 4, 30, tzinfo=UTC)  # noqa: E731 - 10:00 IST
    assert not window.contains(at(26))  # a Monday, but the exchange is shut
    assert window.contains(at(27))
    assert window.contains(at(28))  # never seen: assumed to trade
    assert not window.contains(at(31))  # a Saturday

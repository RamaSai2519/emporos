"""Domain tick types: correct by construction (UTC-only timestamps, positive prices)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.ticks import RawTick, Tick

NOW = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)
IST = timezone(timedelta(hours=5, minutes=30))


def raw(**overrides: object) -> RawTick:
    fields: dict[str, object] = {
        "exchange": Exchange.NSE,
        "token": "3045",
        "exchange_ts": NOW,
        "ltp": Money.of("996.20"),
        "sequence": 1,
    }
    return RawTick(**{**fields, **overrides})  # type: ignore[arg-type]


def tick(**overrides: object) -> Tick:
    fields: dict[str, object] = {
        "instrument_id": "NSE:3045",
        "exchange_ts": NOW,
        "received_ts": NOW,
        "ltp": Money.of("996.20"),
        "sequence": 1,
    }
    return Tick(**{**fields, **overrides})  # type: ignore[arg-type]


def test_valid_ticks_construct_with_sensible_defaults() -> None:
    assert raw().volume is None and raw().quote is None
    assert tick().out_of_order is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"exchange_ts": datetime(2026, 9, 18, 10, 0)},  # naive
        {"exchange_ts": datetime(2026, 9, 18, 10, 0, tzinfo=IST)},  # not UTC
        {"token": ""},
        {"ltp": Money.zero()},
        {"ltp": Money.of("-1")},
        {"volume": -1},
    ],
)
def test_a_raw_tick_rejects_bad_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="."):
        raw(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exchange_ts": datetime(2026, 9, 18, 10, 0)},
        {"received_ts": datetime(2026, 9, 18, 10, 0, tzinfo=IST)},
        {"ltp": Money.zero()},
    ],
)
def test_a_tick_rejects_bad_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="."):
        tick(**overrides)

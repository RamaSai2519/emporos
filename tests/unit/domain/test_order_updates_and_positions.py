from datetime import UTC, datetime

import pytest

from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position

TS = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _update(**overrides: object) -> OrderUpdate:
    fields: dict[str, object] = {
        "instrument_id": "NSE:1001",
        "side": OrderSide.BUY,
        "status": OrderUpdateStatus.FILLED,
        "quantity": 10,
        "filled_quantity": 10,
        "ts": TS,
    }
    return OrderUpdate(**(fields | overrides))  # type: ignore[arg-type]


def test_terminal_statuses_are_the_ones_that_end_an_order() -> None:
    terminal = {s for s in OrderUpdateStatus if s.is_terminal}
    assert terminal == {
        OrderUpdateStatus.FILLED,
        OrderUpdateStatus.CANCELLED,
        OrderUpdateStatus.REJECTED,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ts": datetime(2026, 1, 5, 4, 0)}, "UTC"),
        ({"quantity": -1}, "negative"),
        ({"filled_quantity": -1}, "negative"),
        ({"filled_quantity": 11}, "beyond"),
    ],
)
def test_invalid_updates_are_refused(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _update(**overrides)


def test_an_update_carries_the_optional_price_and_tag() -> None:
    update = _update(average_price=Money.of("100.5"), ordertag="Sabc")
    assert update.average_price == Money.of("100.5") and update.ordertag == "Sabc"


def test_a_flat_position_is_neither_long_nor_open() -> None:
    flat = Position.flat("NSE:1001")
    assert flat.is_flat and not flat.is_long and flat.average_price == Money.zero()


def test_a_long_position_reports_itself() -> None:
    long = Position("NSE:1001", 5, Money.of("100"))
    assert long.is_long and not long.is_flat

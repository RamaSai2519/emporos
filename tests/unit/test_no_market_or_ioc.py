"""No code path can emit a MARKET or IOC order (Decision 8, EM-81).

Enforced three ways, because a runtime check alone would only catch a path a test happens to run:
the types have no such member; no string literal anywhere in the source tree names one, so no code
can build one by spelling it; and the wire request the AngelOne adapter produces for every order
this platform can create carries only a limit-family order type and DAY validity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.broker.angelone.mapping import InstrumentLocator, OrderRequestMapper
from emporos.broker.models import PlaceOrderRequest, Validity
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.instruments.cache import InstrumentCache
from tests.support.architecture import string_constants
from tests.support.fakes import make_instrument

FORBIDDEN = {"MARKET", "IOC", "MKT", "STOPLOSS_MARKET", "STOP_LOSS_MARKET"}


def test_the_types_cannot_express_a_forbidden_order() -> None:
    assert {t.value for t in OrderType} == {"LIMIT", "STOPLOSS_LIMIT"}
    assert {v.value for v in Validity} == {"DAY"}


def test_no_source_file_spells_a_forbidden_order_type() -> None:
    assert string_constants(FORBIDDEN) == []


def test_the_scanner_catches_a_planted_violation(tmp_path: Path) -> None:
    package = tmp_path / "emporos"
    package.mkdir()
    (package / "sneaky.py").write_text('BODY = {"ordertype": "MARKET", "duration": " ioc "}\n')
    (package / "fine.py").write_text('BODY = {"ordertype": "LIMIT"}\n')
    hits = string_constants(FORBIDDEN, tmp_path)
    assert [(h.path, h.what) for h in hits] == [
        ("emporos/sneaky.py", "MARKET"),
        ("emporos/sneaky.py", " ioc "),
    ]


@pytest.mark.parametrize("order_type", list(OrderType))
def test_every_wire_request_is_a_limit_order_valid_for_the_day(order_type: OrderType) -> None:
    trigger = Money.of("99") if order_type is OrderType.STOPLOSS_LIMIT else None
    request = PlaceOrderRequest(
        "NSE:3045", OrderSide.BUY, order_type, 1, Money.of("100"), "TAG", trigger_price=trigger
    )
    instrument = make_instrument("3045", symbol="SBIN-EQ")
    body = OrderRequestMapper(InstrumentLocator(InstrumentCache([instrument]))).place(request)
    assert body["ordertype"] in {"LIMIT", "STOPLOSS_LIMIT"}
    assert body["duration"] == "DAY"

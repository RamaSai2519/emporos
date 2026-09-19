from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128
from pydantic import ValidationError

from emporos.domain.money import Money
from emporos.domain.orders import OrderType
from emporos.persistence.money_codec import MoneyCodec
from emporos.persistence.records import OrderRecord
from tests.support.records import RecordFactory


def test_money_round_trips_exactly_through_decimal128() -> None:
    codec = MoneyCodec()
    money = Money.of("1234567.8901")

    encoded = codec.encode(money)

    assert isinstance(encoded, Decimal128)
    assert codec.decode(encoded) == money


@pytest.mark.parametrize("source", [Decimal("1.5"), "1.5", 3])
def test_decode_accepts_exact_sources(source: object) -> None:
    assert MoneyCodec().decode(source) == Money.of(source)  # type: ignore[arg-type]


def test_decode_passes_money_through() -> None:
    money = Money.of(7)

    assert MoneyCodec().decode(money) is money


@pytest.mark.parametrize("bad", [0.1, True, None, [1]])
def test_decode_rejects_floats_and_other_types(bad: object) -> None:
    with pytest.raises(ValueError, match="floats are forbidden"):
        MoneyCodec().decode(bad)


def test_a_record_stores_money_as_decimal128_and_reads_it_back() -> None:
    order = RecordFactory().order()

    document = order.to_document()
    restored = OrderRecord.model_validate(document)

    assert isinstance(document["limit_price"], Decimal128)
    assert document["trigger_price"] is None
    assert restored == order


def test_a_record_rejects_a_float_price() -> None:
    document = RecordFactory().order().to_document()
    document["limit_price"] = 101.35

    with pytest.raises(ValidationError):
        OrderRecord.model_validate(document)


def test_a_record_cannot_represent_a_market_order() -> None:
    document = RecordFactory().order().to_document()
    document["order_type"] = "MARKET"

    with pytest.raises(ValidationError):
        OrderRecord.model_validate(document)
    assert OrderType.LIMIT.value == "LIMIT"


def test_record_extra_fields_pass_through_unchanged() -> None:
    document = RecordFactory().order().to_document() | {"note": "kept"}

    assert OrderRecord.model_validate(document).to_document()["note"] == "kept"

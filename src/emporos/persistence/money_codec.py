"""Money ⇄ BSON `Decimal128` (plan.md §6: money is `Decimal128`, never float).

`MoneyField` plugs the codec into pydantic records, so a record carries a
`Money` in memory and a `Decimal128` in the document with no per-repository
conversion code. Floats are rejected on the way in.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from bson.decimal128 import Decimal128
from pydantic import BeforeValidator, PlainSerializer

from emporos.domain.money import Money


class MoneyCodec:
    def encode(self, money: Money) -> Decimal128:
        return Decimal128(money.amount)

    def decode(self, value: object) -> Money:
        if isinstance(value, Money):
            return value
        if isinstance(value, Decimal128):
            return Money(value.to_decimal())
        if isinstance(value, Decimal | int | str) and not isinstance(value, bool):
            return Money.of(value)
        raise ValueError(f"cannot read money from {type(value).__name__}; floats are forbidden")


_CODEC = MoneyCodec()


def _serialize(money: Money) -> Decimal128:
    return _CODEC.encode(money)


MoneyField = Annotated[
    Money,
    BeforeValidator(_CODEC.decode),
    PlainSerializer(_serialize, return_type=Any),
]

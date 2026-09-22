from __future__ import annotations

from decimal import Decimal

from emporos.domain.money import Money
from emporos.opportunity.capital import PRODUCTION_CAPITAL


def test_production_capital_is_exactly_fifty_thousand_rupees() -> None:
    assert Money(Decimal(50_000)) == PRODUCTION_CAPITAL

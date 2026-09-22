"""The fixed production-capital scenario (EM-152 / EM-157): every production-capital backtest,
walk-forward window and paper/live conservative-allocation run starts from exactly this figure.
A single named constant, not a config default, so nothing can drift it accidentally — changing
the production-capital scenario is a deliberate code change, reviewed like any other.
"""

from __future__ import annotations

from decimal import Decimal

from emporos.domain.money import Money

PRODUCTION_CAPITAL = Money(Decimal(50_000))

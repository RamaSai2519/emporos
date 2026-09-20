"""How a backtest's simulated exchange behaves, as data (plan.md §10: configurable fills).

Strict like every other config in the project: unknown keys are refused, rates are quoted strings
or integers (never YAML floats). The defaults are the conservative ones; each departure from them
is an explicit, reviewable setting that a report repeats back.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from emporos.backtest.fill_model import (
    BarFillModel,
    BarParticipation,
    FillPrice,
    GapAwareSlippage,
    LimitCrossing,
    ThroughBar,
    TouchBar,
)
from emporos.backtest.rejects import RejectPolicy, SeededRejectRate
from emporos.strategies.config import ExactDecimal, NonNegativeInt

_CROSSINGS: dict[str, type[LimitCrossing]] = {"through": ThroughBar, "touch": TouchBar}


class FillSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    crossing: Literal["through", "touch"] = "through"
    participation: ExactDecimal = Field(default=Decimal("0.1"), gt=0, le=1)
    # None: a fill is at the order's own limit. A number: gap-aware price, moved against the order.
    slippage_bps: ExactDecimal | None = Field(default=None, ge=0)
    reject_rate_bps: NonNegativeInt = Field(default=0, le=10_000)
    reject_seed: NonNegativeInt = 0
    # The broker's own end-of-day square-off closes what our orders did not, this far against us.
    forced_close_penalty_bps: ExactDecimal = Field(default=Decimal(10), ge=0)

    def describe(self) -> str:
        price = (
            "at the order's own limit"
            if self.slippage_bps is None
            else (f"gap-aware with {self.slippage_bps} bps slippage")
        )
        return (
            f"fills: {self.crossing} crossing, {price}, {self.participation} of bar volume, "
            f"{self.reject_rate_bps} bps rejected (seed {self.reject_seed}), forced square-off "
            f"{self.forced_close_penalty_bps} bps against"
        )


class FillModelFactory:
    """Builds the exchange's policies from settings. A new crossing rule is one entry in the
    registry above, not an edit here."""

    def model(self, settings: FillSettings) -> BarFillModel:
        price: FillPrice | None = (
            None if settings.slippage_bps is None else GapAwareSlippage(settings.slippage_bps)
        )
        return BarFillModel(
            crossing=_CROSSINGS[settings.crossing](),
            price=price,
            liquidity=BarParticipation(settings.participation),
        )

    def rejects(self, settings: FillSettings) -> RejectPolicy:
        return SeededRejectRate(settings.reject_rate_bps, settings.reject_seed)

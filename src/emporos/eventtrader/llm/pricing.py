"""What a call costs. Prices are declared, not assumed: a model with no price cannot be spent on."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["ModelPrice", "PriceTable"]

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPrice:
    usd_per_million_input: Decimal
    usd_per_million_output: Decimal

    def __post_init__(self) -> None:
        if self.usd_per_million_input < 0 or self.usd_per_million_output < 0:
            raise ValueError("a price cannot be negative")

    def cost_usd(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (
            Decimal(tokens_in) * self.usd_per_million_input
            + Decimal(tokens_out) * self.usd_per_million_output
        ) / _MILLION


@dataclass(frozen=True)
class PriceTable:
    prices: Mapping[str, ModelPrice]
    usd_inr: Decimal  # the declared rate token cost is converted to rupees at

    def __post_init__(self) -> None:
        if self.usd_inr <= 0:
            raise ValueError("the USD/INR rate must be positive")

    def price(self, model: str) -> ModelPrice:
        try:
            return self.prices[model]
        except KeyError:
            raise ValueError(f"no declared price for model {model!r}") from None

    def cost_usd(self, model: str, tokens_in: int, tokens_out: int) -> Decimal:
        return self.price(model).cost_usd(tokens_in, tokens_out)

    def cost_inr(self, model: str, tokens_in: int, tokens_out: int) -> Decimal:
        return self.cost_usd(model, tokens_in, tokens_out) * self.usd_inr

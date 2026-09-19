"""The `Strategy` contract (plan.md §9).

A strategy DECIDES; it never acts. The runner delivers closed bars (or ticks) to `on_market_data`
and, after each one, calls `generate_signal` repeatedly until it returns None. Handlers are
synchronous and total-ordered: a fill's `on_order_update` is delivered before the next bar.

Determinism is part of the contract: no wall-clock reads (use `ctx.clock`), no I/O, and randomness
only from `ctx.rng`. `tests/unit/strategies/test_purity.py` enforces the first two mechanically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from emporos.domain.candles import Candle
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.signals import Signal
from emporos.domain.ticks import Tick
from emporos.strategies.config import ResolvedStrategyConfig, StrategyParameters
from emporos.strategies.context import StrategyContext


class Strategy(ABC):
    """Subclasses declare `name` (the registry key, matching the YAML `name:`) and
    `parameters_model` (the strict schema for the YAML `parameters:` block)."""

    name: ClassVar[str]
    parameters_model: ClassVar[type[StrategyParameters]]

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if config.name != self.name:
            raise ValueError(f"{type(self).__name__} cannot run a '{config.name}' configuration")
        self._config = config

    @property
    def config(self) -> ResolvedStrategyConfig:
        return self._config

    @abstractmethod
    def initialize(self, ctx: StrategyContext) -> None:
        """Called once before any market data. Keep the context; warm up from `ctx.history`."""

    @abstractmethod
    def on_market_data(self, event: Candle | Tick) -> None:
        """A closed bar or a tick. Update state; queue any signal for `generate_signal`."""

    @abstractmethod
    def generate_signal(self) -> Signal | None:
        """The next queued signal, or None when there is nothing (more) to say."""

    def on_order_update(self, update: OrderUpdate) -> None:  # noqa: B027 — optional hook
        """An order this strategy's signals caused changed state."""

    def on_session_end(self) -> None:  # noqa: B027 — optional hook
        """The trading session is over; a chance to emit final exit signals."""

    def on_shutdown(self) -> None:  # noqa: B027 — optional hook
        """The run is ending. Release nothing external: strategies hold no resources."""

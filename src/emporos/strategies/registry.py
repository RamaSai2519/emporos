"""Strategy registry: names to implementations (plan.md §9).

Injected, never a module-level singleton, so tests and the backtester each build their own.
Adding a strategy is adding a class (and a YAML file); nothing in here changes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from emporos.strategies.base import Strategy
from emporos.strategies.config import ResolvedStrategyConfig, StrategyParameters
from emporos.strategies.metadata import StrategyMetadata

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Registered without explicit metadata (legacy call sites, or a strategy still being wired up):
# research-stage, no declared regime/timeframe applicability beyond "whatever its config says".
_UNKNOWN_METADATA = StrategyMetadata(
    version="unknown",
    supported_timeframes=frozenset(),
    supported_regimes=frozenset(),
    description="no metadata declared at registration",
)


class UnknownStrategyError(LookupError):
    """No strategy is registered under that name."""


class DuplicateStrategyError(ValueError):
    """A second strategy tried to take a name that is already registered."""


class StrategyRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[Strategy]] = {}
        self._metadata: dict[str, StrategyMetadata] = {}

    def register(self, strategy: type[Strategy], metadata: StrategyMetadata | None = None) -> None:
        name = self._name_of(strategy)
        if name in self._classes:
            raise DuplicateStrategyError(
                f"'{name}' is already registered to {self._classes[name].__name__}"
            )
        self._classes[name] = strategy
        self._metadata[name] = metadata if metadata is not None else _UNKNOWN_METADATA

    def register_all(
        self, strategies: Iterable[type[Strategy] | tuple[type[Strategy], StrategyMetadata]]
    ) -> None:
        for entry in strategies:
            if isinstance(entry, tuple):
                self.register(entry[0], entry[1])
            else:
                self.register(entry)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._classes))

    def get(self, name: str) -> type[Strategy]:
        try:
            return self._classes[name]
        except KeyError:
            known = ", ".join(self.names()) or "none"
            raise UnknownStrategyError(f"unknown strategy '{name}' (registered: {known})") from None

    def parameters_model(self, name: str) -> type[StrategyParameters]:
        return self.get(name).parameters_model

    def metadata(self, name: str) -> StrategyMetadata:
        self.get(name)  # raises UnknownStrategyError with the same message for a bad name
        return self._metadata[name]

    def create(self, config: ResolvedStrategyConfig) -> Strategy:
        strategy = self.get(config.name)
        if not isinstance(config.parameters, strategy.parameters_model):
            raise TypeError(
                f"{config.name} needs {strategy.parameters_model.__name__} parameters, "
                f"got {type(config.parameters).__name__}"
            )
        return strategy(config)

    @staticmethod
    def _name_of(strategy: type[Strategy]) -> str:
        if not (isinstance(strategy, type) and issubclass(strategy, Strategy)):
            raise TypeError(f"{strategy!r} is not a Strategy subclass")
        if strategy.__abstractmethods__:
            raise TypeError(f"{strategy.__name__} is abstract and cannot be registered")
        name = getattr(strategy, "name", None)
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"{strategy.__name__}.name must be a lowercase identifier")
        if not hasattr(strategy, "parameters_model"):
            raise ValueError(f"{strategy.__name__} must declare parameters_model")
        return name

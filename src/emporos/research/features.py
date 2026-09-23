"""Causal feature computation and a versioned, content-hashed feature registry (EM-178).

A feature must compute from information available at timestamp t and nothing later. That is
enforced structurally, not just documented: `Feature.compute` receives a `CausalHistory`, a view
over the full bar series that raises on any attempt to read past the bar being evaluated, so a
feature that only ever reads through the view it is given cannot see the future even by mistake —
the same "make the forbidden thing unrepresentable" approach this codebase uses for order types.

`FeatureDefinition` is what gets content-hashed and stored per trial (`core.hashing`, the same
mechanism EM-177's `ResearchProvenance` uses for datasets), so a later run can prove exactly which
computation, with which parameters, produced a result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, overload

from emporos.core.hashing import Canonical, content_hash
from emporos.domain.candles import Candle


class CausalHistory(Sequence[Candle]):
    """A read-only view of `history[: index + 1]`: index `index` and everything before it, nothing
    after. Built once per bar by `FeatureSeries`, not by feature code itself."""

    def __init__(self, history: Sequence[Candle], index: int) -> None:
        if index < 0 or index >= len(history):
            raise IndexError("index must lie within history")
        self._history = history
        self._length = index + 1

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, key: int) -> Candle: ...
    @overload
    def __getitem__(self, key: slice) -> Sequence[Candle]: ...

    def __getitem__(self, key: int | slice) -> Candle | Sequence[Candle]:
        if isinstance(key, slice):
            return list(self._history[: self._length])[key]
        index = key + self._length if key < 0 else key
        if not 0 <= index < self._length:
            raise IndexError("index out of causal range")
        return self._history[index]

    @property
    def last(self) -> Candle:
        return self._history[self._length - 1]


class Feature(Protocol):
    """Computes one feature value causally from a `CausalHistory`."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        """None while there is not yet enough history for this feature to be defined."""
        ...


@dataclass(frozen=True)
class FeatureDefinition:
    """What a feature IS, versioned: two definitions with the same name and a different `version`
    are different features and are never compared against each other."""

    name: str
    version: str
    description: str
    parameters: dict[str, Canonical]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a feature needs a name")
        if not self.version:
            raise ValueError("a feature needs a version")

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def content_hash(self) -> str:
        document: dict[str, Canonical] = {
            "name": self.name, "version": self.version, "parameters": self.parameters,
        }  # fmt: skip
        return content_hash(document)


class DuplicateFeatureError(Exception):
    """A feature with this name+version is already registered; the registry never overwrites."""


class FeatureRegistry:
    """Every feature a study may reference, keyed by `name@version`. Registering the same
    name+version twice is refused, so a feature's behaviour for a given version can never
    quietly change under research already run against it (open/closed: a changed feature ships
    as a new version)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[FeatureDefinition, Feature]] = {}

    def register(self, definition: FeatureDefinition, feature: Feature) -> None:
        if definition.key in self._entries:
            raise DuplicateFeatureError(definition.key)
        self._entries[definition.key] = (definition, feature)

    def get(self, name: str, version: str) -> tuple[FeatureDefinition, Feature]:
        try:
            return self._entries[f"{name}@{version}"]
        except KeyError:
            raise KeyError(f"no feature registered as {name}@{version}") from None

    def all(self) -> tuple[FeatureDefinition, ...]:
        return tuple(definition for definition, _ in self._entries.values())


class FeatureSeries:
    """Walks a bar history forward, computing one feature causally at every bar."""

    def __init__(self, feature: Feature) -> None:
        self._feature = feature

    def compute(self, history: Sequence[Candle]) -> list[Decimal | None]:
        return [self._feature.compute(CausalHistory(history, i)) for i in range(len(history))]

"""How many spreads may be open at once (EM-226): a ladder is opened one spread at a time, and
the limit may depend on the day (a third slot that opens only under a stated conviction rule)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from emporos.options.chain import ChainSnapshot

__all__ = ["DepthPolicy", "FixedDepth"]


class DepthPolicy(Protocol):
    def limit(self, snapshot: ChainSnapshot) -> int:
        """The most spreads that may be open after today's entry."""
        ...


@dataclass(frozen=True)
class FixedDepth:
    depth: int = 1

    def __post_init__(self) -> None:
        if self.depth < 1:
            raise ValueError("at least one spread must be allowed")

    def limit(self, snapshot: ChainSnapshot) -> int:
        return self.depth

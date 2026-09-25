"""Where chains come from (EM-226). The backtester asks a `ChainSource`, never a file or a vendor,
so a licensed end-of-day feed plugs in later without touching the backtester."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from emporos.options.chain import ChainSnapshot

__all__ = ["ChainSource", "InMemoryChainSource"]


class ChainSource(Protocol):
    def days(self) -> Sequence[date]:
        """Every trading day the source holds a chain for, oldest first."""
        ...

    def snapshot(self, day: date) -> ChainSnapshot | None:
        """The chain at that day's close, or none if the source has nothing for it."""
        ...


class InMemoryChainSource:
    """A source over snapshots already in memory: for tests and small fixtures."""

    def __init__(self, snapshots: Iterable[ChainSnapshot]) -> None:
        self._by_day: dict[date, ChainSnapshot] = {}
        for snapshot in snapshots:
            if snapshot.day in self._by_day:
                raise ValueError(f"two snapshots for {snapshot.day.isoformat()}")
            self._by_day[snapshot.day] = snapshot

    def days(self) -> Sequence[date]:
        return sorted(self._by_day)

    def snapshot(self, day: date) -> ChainSnapshot | None:
        return self._by_day.get(day)

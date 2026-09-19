"""A strategy's own positions, as the context shows them.

Only positions attributable to THIS strategy are exposed. The source is the portfolio
(Phase 13); the strategy sees this narrow read-only view and nothing about how it is kept.
"""

from __future__ import annotations

from typing import Protocol

from emporos.domain.positions import Position


class PositionView(Protocol):
    def position(self, instrument_id: str) -> Position:
        """The strategy's net position; flat (never None) when it holds nothing."""
        ...

    def open_positions(self) -> tuple[Position, ...]: ...


class FlatPositions:
    """A view that is always flat: a strategy with nothing open, or one not yet wired to a book."""

    def position(self, instrument_id: str) -> Position:
        return Position.flat(instrument_id)

    def open_positions(self) -> tuple[Position, ...]:
        return ()

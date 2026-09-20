"""Tick sizes from the instrument master, cached: a tick is fixed for an instrument's lifetime."""

from __future__ import annotations

from emporos.domain.money import Money
from emporos.persistence.repositories import InstrumentRepository


class UnknownInstrumentTickError(LookupError):
    """No instrument with that id is in the master, so no price for it can be validated."""


class InstrumentTickSizes:
    def __init__(self, instruments: InstrumentRepository) -> None:
        self._instruments = instruments
        self._cache: dict[str, Money] = {}

    async def tick_size(self, instrument_id: str) -> Money:
        cached = self._cache.get(instrument_id)
        if cached is not None:
            return cached
        record = await self._instruments.get(instrument_id)
        if record is None:
            raise UnknownInstrumentTickError(instrument_id)
        self._cache[instrument_id] = record.tick_size
        return record.tick_size

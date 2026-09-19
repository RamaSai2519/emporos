"""In-memory instrument resolution: O(1) lookup by token, by symbol, and by id (plan.md §8).

Strategies get an `InstrumentResolver` (a domain Protocol) and never see a token
literal. The cache is refreshed after every sync without a restart: `refresh`
builds a complete new index off to the side and swaps one reference, so a reader
never observes a half-updated mix of old and new instruments.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

from emporos.domain.instruments import Exchange, Instrument, UnknownInstrumentError
from emporos.instruments.store import InstrumentMasterStore

K = TypeVar("K")


@dataclass(frozen=True)
class _Index:
    by_token: dict[tuple[Exchange, str], Instrument] = field(default_factory=dict)
    by_symbol: dict[tuple[Exchange, str], Instrument] = field(default_factory=dict)
    by_id: dict[str, Instrument] = field(default_factory=dict)

    @classmethod
    def build(cls, instruments: Iterable[Instrument]) -> _Index:
        items = list(instruments)
        return cls(
            by_token={(i.exchange, i.token): i for i in items},
            by_symbol={(i.exchange, i.tradingsymbol): i for i in items},
            by_id={i.instrument_id: i for i in items},
        )


class InstrumentCache:
    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._index = _Index.build(instruments)

    def __len__(self) -> int:
        return len(self._index.by_id)

    def refresh(self, instruments: Iterable[Instrument]) -> None:
        self._index = _Index.build(instruments)

    async def load_from(self, store: InstrumentMasterStore) -> None:
        """Populate from the persisted master — used at startup and after each sync."""
        self.refresh(await store.load_current())

    def by_token(self, exchange: Exchange, token: str) -> Instrument:
        return self._find(
            self._index.by_token, (exchange, token), f"{exchange.value} token {token}"
        )

    def by_symbol(self, exchange: Exchange, tradingsymbol: str) -> Instrument:
        return self._find(
            self._index.by_symbol, (exchange, tradingsymbol), f"{exchange.value}:{tradingsymbol}"
        )

    def by_id(self, instrument_id: str) -> Instrument:
        return self._find(self._index.by_id, instrument_id, instrument_id)

    @staticmethod
    def _find(index: Mapping[K, Instrument], key: K, description: str) -> Instrument:
        try:
            return index[key]
        except KeyError:
            raise UnknownInstrumentError(f"unknown instrument {description}") from None

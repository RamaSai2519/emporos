"""Persisting the instrument master: versioned history plus an atomic swap of the current set.

`apply` runs the whole diff — current-set changes and version records — in ONE
transaction, so `instruments` is never partially updated and never disagrees with
`instrument_versions`, even if the process dies part-way through.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Protocol, TypeVar

from pymongo.asynchronous.client_session import AsyncClientSession

from emporos.domain.instruments import Exchange, Instrument
from emporos.instruments.differ import InstrumentDiff
from emporos.persistence.records import InstrumentRecord, InstrumentVersionRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.transactions import TransactionRunner

T = TypeVar("T")
_BATCH = 1000


class InstrumentMasterStore(Protocol):
    async def load_current(self) -> list[Instrument]: ...

    async def apply(self, diff: InstrumentDiff, at: datetime) -> None: ...


class InstrumentRecordMapper:
    """`Instrument` ⇄ its persisted shapes."""

    def to_record(self, instrument: Instrument) -> InstrumentRecord:
        return InstrumentRecord(
            _id=instrument.instrument_id,
            exchange=instrument.exchange.value,
            token=instrument.token,
            tradingsymbol=instrument.tradingsymbol,
            name=instrument.name,
            lot_size=instrument.lot_size,
            tick_size=instrument.tick_size,
        )

    def to_domain(self, record: InstrumentRecord) -> Instrument:
        return Instrument(
            exchange=Exchange(record.exchange),
            token=record.token,
            tradingsymbol=record.tradingsymbol,
            name=record.name,
            lot_size=record.lot_size,
            tick_size=record.tick_size,
        )

    def to_version(self, instrument: Instrument, valid_from: datetime) -> InstrumentVersionRecord:
        return InstrumentVersionRecord(
            _id=f"{instrument.instrument_id}@{valid_from.isoformat()}",
            instrument_id=instrument.instrument_id,
            exchange=instrument.exchange.value,
            token=instrument.token,
            tradingsymbol=instrument.tradingsymbol,
            name=instrument.name,
            lot_size=instrument.lot_size,
            tick_size=instrument.tick_size,
            valid_from=valid_from,
        )


class MongoInstrumentMasterStore:
    def __init__(
        self,
        transactions: TransactionRunner,
        instruments: InstrumentRepository,
        versions: InstrumentVersionRepository,
        mapper: InstrumentRecordMapper | None = None,
    ) -> None:
        self._transactions = transactions
        self._instruments = instruments
        self._versions = versions
        self._mapper = mapper or InstrumentRecordMapper()

    async def load_current(self) -> list[Instrument]:
        return [self._mapper.to_domain(record) for record in await self._instruments.all()]

    async def apply(self, diff: InstrumentDiff, at: datetime) -> None:
        if diff.is_empty:
            return

        async def write(session: AsyncClientSession) -> None:
            await self._write_diff(diff, at, session)

        await self._transactions.run(write)

    async def _write_diff(
        self, diff: InstrumentDiff, at: datetime, session: AsyncClientSession
    ) -> None:
        mapper = self._mapper
        after_change = [change.after for change in diff.changed]
        ended = [i.instrument_id for i in diff.removed] + [
            change.before.instrument_id for change in diff.changed
        ]
        await self._versions.close_open_versions(ended, at, session=session)
        await self._instruments.delete_many(
            [i.instrument_id for i in diff.removed], session=session
        )
        for change in diff.changed:
            await self._instruments.replace(mapper.to_record(change.after), session=session)
        for batch in _batches([*diff.added]):
            await self._instruments.insert_many(
                [mapper.to_record(i) for i in batch], session=session
            )
        for batch in _batches([*diff.added, *after_change]):
            await self._versions.insert_many(
                [mapper.to_version(i, at) for i in batch], session=session
            )


def _batches(items: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), _BATCH):
        yield items[start : start + _BATCH]

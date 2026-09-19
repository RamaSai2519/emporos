"""Persisting the instrument master: append-only history plus an atomic swap of the current set.

Two write paths, both all-or-nothing:

* An incremental diff (the daily case) runs in ONE transaction — current-set changes
  and the closed history records for what they supersede — so `instruments` is never
  partially updated and never disagrees with `instrument_versions`.
* The initial load into an empty master is too large for a transaction, so it is
  staged and swapped in with an atomic rename (`StagedCollectionLoader`).

History is "on change only": when a definition is replaced or removed, the old
definition is appended to `instrument_versions` as a closed `[valid_from, valid_to)`
record; the current record carries its own `valid_from`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pymongo.asynchronous.client_session import AsyncClientSession

from emporos.domain.instruments import Exchange, Instrument
from emporos.instruments.differ import InstrumentDiff
from emporos.persistence.records import InstrumentRecord, InstrumentVersionRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.staged_load import StagedCollectionLoader
from emporos.persistence.transactions import TransactionRunner


class InstrumentMasterStore(Protocol):
    async def load_current(self) -> list[Instrument]: ...

    async def apply(self, diff: InstrumentDiff, at: datetime) -> None: ...


class InstrumentRecordMapper:
    """`Instrument` ⇄ its persisted shapes."""

    def to_record(self, instrument: Instrument, valid_from: datetime) -> InstrumentRecord:
        return InstrumentRecord(
            _id=instrument.instrument_id,
            exchange=instrument.exchange.value,
            token=instrument.token,
            tradingsymbol=instrument.tradingsymbol,
            name=instrument.name,
            lot_size=instrument.lot_size,
            tick_size=instrument.tick_size,
            valid_from=valid_from,
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

    def to_closed_version(
        self, superseded: InstrumentRecord, valid_to: datetime
    ) -> InstrumentVersionRecord:
        return InstrumentVersionRecord(
            _id=f"{superseded.id}@{superseded.valid_from.isoformat()}",
            instrument_id=superseded.id,
            exchange=superseded.exchange,
            token=superseded.token,
            tradingsymbol=superseded.tradingsymbol,
            name=superseded.name,
            lot_size=superseded.lot_size,
            tick_size=superseded.tick_size,
            valid_from=superseded.valid_from,
            valid_to=valid_to,
        )


class MongoInstrumentMasterStore:
    def __init__(
        self,
        transactions: TransactionRunner,
        instruments: InstrumentRepository,
        versions: InstrumentVersionRepository,
        bulk_loader: StagedCollectionLoader[InstrumentRecord],
        mapper: InstrumentRecordMapper | None = None,
    ) -> None:
        self._transactions = transactions
        self._instruments = instruments
        self._versions = versions
        self._bulk_loader = bulk_loader
        self._mapper = mapper or InstrumentRecordMapper()

    async def load_current(self) -> list[Instrument]:
        return [self._mapper.to_domain(record) for record in await self._instruments.all()]

    async def apply(self, diff: InstrumentDiff, at: datetime) -> None:
        if diff.is_empty:
            return
        if not (diff.changed or diff.removed) and await self._instruments.count() == 0:
            await self._bulk_load(diff, at)
            return

        async def write(session: AsyncClientSession) -> None:
            await self._write_incremental(diff, at, session)

        await self._transactions.run(write)

    async def _bulk_load(self, diff: InstrumentDiff, at: datetime) -> None:
        await self._bulk_loader.replace_all([self._mapper.to_record(i, at) for i in diff.added])

    async def _write_incremental(
        self, diff: InstrumentDiff, at: datetime, session: AsyncClientSession
    ) -> None:
        mapper = self._mapper
        superseded_ids = [c.before.instrument_id for c in diff.changed] + [
            i.instrument_id for i in diff.removed
        ]
        superseded = await self._instruments.get_many(superseded_ids, session=session)
        await self._versions.insert_many(
            [mapper.to_closed_version(record, at) for record in superseded], session=session
        )
        await self._instruments.delete_many(
            [i.instrument_id for i in diff.removed], session=session
        )
        for change in diff.changed:
            await self._instruments.replace(mapper.to_record(change.after, at), session=session)
        await self._instruments.insert_many(
            [mapper.to_record(i, at) for i in diff.added], session=session
        )

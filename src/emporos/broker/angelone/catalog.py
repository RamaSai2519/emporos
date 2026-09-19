"""`get_instruments` for Angel One: download the master, validate it, return domain instruments."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.domain.instruments import Instrument
from emporos.instruments.sync import MasterSource
from emporos.instruments.validator import InstrumentMasterValidator


class DownloadedInstrumentCatalog:
    """Validates against the row-count gate with no prior master, so only field sanity applies;
    the sync pipeline (Phase 3) remains the guard for what gets STORED."""

    def __init__(self, source: MasterSource, validator: InstrumentMasterValidator) -> None:
        self._source = source
        self._validator = validator

    async def load(self) -> Sequence[Instrument]:
        master = await self._source.download()
        return self._validator.validate(master, current_count=len(master.rows)).instruments

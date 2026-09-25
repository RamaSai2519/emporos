"""5-minute bars from the cold archive, through the vault (EM-239, L-D3): the sealed days cannot
be read."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.cold_storage import DEFAULT_COLD_DIR
from emporos.cli.vault_files import VaultFiles
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cache import ColdArchiveFiles, FileCandleReader

__all__ = ["VaultedIntradayBars"]


class VaultedIntradayBars:
    """`BarLoader` over the cold archive's month files, wrapped in the vault reader."""

    def __init__(self, reader: VaultedCandleReader) -> None:
        self._reader = reader

    @staticmethod
    def from_settings(settings: Settings, cold_dir: Path | None = None) -> VaultedIntradayBars:
        files = ColdArchiveFiles(Path(cold_dir or settings.cold_archive_dir or DEFAULT_COLD_DIR))
        return VaultedIntradayBars(
            VaultedCandleReader(FileCandleReader([files], memoize=False), VaultFiles().load())
        )

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        start = datetime.combine(first, time(0, 0), tzinfo=IST)
        end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
        return asyncio.run(self._reader.get_range(instrument_id, Timeframe.M5, start, end))

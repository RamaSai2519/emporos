"""The public Angel One scrip master, read for option contracts only (EM-246).

Unauthenticated plain HTTPS JSON, downloaded once a day by the option recorder. Only NFO
OPTIDX/OPTSTK rows of the underlyings asked for are kept."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import httpx

from emporos.instruments.downloader import MASTER_URL, InstrumentMasterDownloader, RawRow
from emporos.quotes.contract import OPTION_KINDS, ContractBook

__all__ = ["OptionSegmentFilter", "ScripMasterContracts"]

MASTER_TIMEOUT = 120.0  # the file is tens of megabytes


class OptionSegmentFilter:
    def __init__(self, underlyings: Collection[str]) -> None:
        self._names = frozenset(underlyings)

    def accepts(self, row: RawRow) -> bool:
        return (
            row.get("exch_seg") == "NFO"
            and row.get("instrumenttype") in OPTION_KINDS
            and row.get("name") in self._names
        )


class ScripMasterContracts:
    def __init__(
        self,
        underlyings: Collection[str],
        url: str = MASTER_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._underlyings, self._url, self._transport = tuple(underlyings), url, transport

    async def load(self) -> ContractBook:
        async with httpx.AsyncClient(timeout=MASTER_TIMEOUT, transport=self._transport) as client:
            downloader = InstrumentMasterDownloader(
                client, self._url, OptionSegmentFilter(self._underlyings)
            )
            master = await downloader.download()
        rows: list[Any] = list(master.rows)
        return ContractBook.from_master_rows(rows)

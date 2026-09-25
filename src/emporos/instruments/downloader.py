"""Download the daily Angel One instrument master (plan.md §8).

The file is unauthenticated plain HTTPS JSON with no publication SLA, so the
downloader treats it as untrusted: any failure becomes a typed error the caller
can handle, never a crash. Only the v1 scope — NSE/BSE cash instruments — is
returned; derivatives, commodities and currency rows are dropped here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from emporos.instruments.errors import MasterDownloadError, MasterFormatError

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

RawRow = Mapping[str, Any]


class SegmentFilter(Protocol):
    def accepts(self, row: RawRow) -> bool: ...


class CashSegmentFilter:
    """Which upstream rows are in scope: NSE/BSE cash (empty `instrumenttype`, no index rows)."""

    def __init__(self, exchanges: frozenset[str] = frozenset({"NSE", "BSE"})) -> None:
        self._exchanges = exchanges

    def accepts(self, row: RawRow) -> bool:
        return row.get("exch_seg") in self._exchanges and row.get("instrumenttype") == ""


@dataclass(frozen=True)
class DownloadedMaster:
    rows: tuple[RawRow, ...]
    upstream_row_count: int


class InstrumentMasterDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str = MASTER_URL,
        segment_filter: SegmentFilter | None = None,
    ) -> None:
        self._client = client
        self._url = url
        self._filter = segment_filter or CashSegmentFilter()

    async def download(self) -> DownloadedMaster:
        body = await self._fetch()
        if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
            raise MasterFormatError("instrument master is not a JSON array of objects")
        rows = tuple(row for row in body if self._filter.accepts(row))
        return DownloadedMaster(rows=rows, upstream_row_count=len(body))

    async def _fetch(self) -> Any:
        try:
            response = await self._client.get(self._url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MasterDownloadError(f"could not download instrument master: {error}") from error
        try:
            return response.json()
        except ValueError as error:
            raise MasterFormatError(f"instrument master is not valid JSON: {error}") from error

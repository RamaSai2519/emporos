import json
from pathlib import Path

import httpx
import pytest

from emporos.core.errors import ErrorClassification
from emporos.instruments.downloader import (
    MASTER_URL,
    CashSegmentFilter,
    InstrumentMasterDownloader,
)
from emporos.instruments.errors import MasterDownloadError, MasterFormatError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "instrument_master_sample.json"


def _downloader(handler: httpx.MockTransport) -> InstrumentMasterDownloader:
    return InstrumentMasterDownloader(httpx.AsyncClient(transport=handler))


def _serving(body: bytes, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


async def test_downloads_parses_and_keeps_only_nse_bse_cash_rows() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=FIXTURE.read_bytes())

    master = await _downloader(httpx.MockTransport(handler)).download()

    assert requested == [MASTER_URL]
    assert master.upstream_row_count == 31
    assert len(master.rows) == 26
    assert {row["exch_seg"] for row in master.rows} == {"NSE", "BSE"}
    assert {row["instrumenttype"] for row in master.rows} == {""}


async def test_network_failure_raises_a_retryable_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(MasterDownloadError) as raised:
        await _downloader(httpx.MockTransport(handler)).download()

    assert raised.value.classification is ErrorClassification.RETRYABLE


async def test_a_server_error_raises_a_typed_download_error() -> None:
    with pytest.raises(MasterDownloadError):
        await _downloader(_serving(b"oops", status=503)).download()


async def test_a_truncated_file_raises_a_format_error() -> None:
    truncated = FIXTURE.read_bytes()[:400]

    with pytest.raises(MasterFormatError, match="not valid JSON"):
        await _downloader(_serving(truncated)).download()


@pytest.mark.parametrize("body", [b'{"data": []}', b"[1, 2, 3]", b'"text"', b"null"])
async def test_a_body_that_is_not_an_array_of_objects_raises_a_format_error(body: bytes) -> None:
    with pytest.raises(MasterFormatError, match="array of objects"):
        await _downloader(_serving(body)).download()


async def test_an_empty_array_downloads_as_zero_rows() -> None:
    master = await _downloader(_serving(json.dumps([]).encode())).download()

    assert master.rows == ()
    assert master.upstream_row_count == 0


def test_the_segment_filter_can_be_narrowed_to_one_exchange() -> None:
    nse_only = CashSegmentFilter(frozenset({"NSE"}))

    assert nse_only.accepts({"exch_seg": "NSE", "instrumenttype": ""})
    assert not nse_only.accepts({"exch_seg": "BSE", "instrumenttype": ""})
    assert not nse_only.accepts({"exch_seg": "NSE", "instrumenttype": "AMXIDX"})

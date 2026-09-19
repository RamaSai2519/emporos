"""The whole instrument pipeline on recorded fixture data — no live network (EM-30).

Real components end to end: downloader (over a mock HTTP transport serving the recorded
sample), validator, differ, the Mongo master store on scratch collections, and the cache.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from emporos.core.clock import FixedClock
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiffer
from emporos.instruments.downloader import InstrumentMasterDownloader
from emporos.instruments.sync import InstrumentSyncService, SyncOutcome
from emporos.instruments.validator import InstrumentMasterValidator, ValidationPolicy
from tests.support.fakes import RecordingAlertSink
from tests.support.instrument_rows import SAMPLE, sample_rows
from tests.support.mongo_instruments import Rig

pytestmark = pytest.mark.integration

DAY_ONE = datetime(2026, 3, 2, 3, 30, tzinfo=UTC)
# The recorded sample is small, so the row floor is lowered; every other rule is the default.
SAMPLE_POLICY = ValidationPolicy(max_invalid_fraction=Decimal("0.10"), min_rows=5)


class Pipeline:
    """One sync run over whatever body the fake upstream currently serves."""

    def __init__(self, rig: Rig) -> None:
        self.body = SAMPLE.read_bytes()
        self.rig = rig
        self.cache = InstrumentCache()
        self.alerts = RecordingAlertSink()
        self.clock = FixedClock(DAY_ONE)

    async def sync(self) -> SyncOutcome:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=self.body))
        async with httpx.AsyncClient(transport=transport) as client:
            service = InstrumentSyncService(
                source=InstrumentMasterDownloader(client),
                validator=InstrumentMasterValidator(SAMPLE_POLICY),
                differ=InstrumentDiffer(),
                store=self.rig.store,
                cache=self.cache,
                alerts=self.alerts,
                clock=self.clock,
            )
            outcome = (await service.run()).outcome
        self.clock.advance(timedelta(days=1))
        return outcome

    def serve_rows(self, rows: list[dict[str, Any]]) -> None:
        self.body = json.dumps(rows).encode()


@pytest.fixture
def pipeline(rig: Rig) -> Pipeline:
    return Pipeline(rig)


async def test_a_valid_master_loads_and_a_second_identical_sync_is_a_no_op(
    pipeline: Pipeline,
) -> None:
    assert await pipeline.sync() is SyncOutcome.APPLIED
    assert await pipeline.sync() is SyncOutcome.NO_CHANGE

    assert len(await pipeline.rig.store.load_current()) == 24  # 26 cash rows, 2 unusable
    assert await pipeline.rig.versions.count() == 0
    assert pipeline.alerts.alerts == []


async def test_instruments_resolve_by_token_and_by_symbol_after_a_sync(
    pipeline: Pipeline,
) -> None:
    await pipeline.sync()

    by_token = pipeline.cache.by_token(Exchange.NSE, "10099")
    by_symbol = pipeline.cache.by_symbol(Exchange.NSE, "GODREJCP-EQ")
    bse = pipeline.cache.by_token(Exchange.BSE, "500020")

    assert by_token is by_symbol
    assert (by_token.name, by_token.lot_size, by_token.tick_size) == (
        "GODREJCP",
        1,
        Money.of("0.05"),
    )
    assert (bse.tradingsymbol, bse.tick_size) == ("BOMDYEING", Money.of("0.05"))


async def test_a_truncated_upstream_file_does_not_wipe_the_master_and_alerts(
    pipeline: Pipeline,
) -> None:
    await pipeline.sync()
    pipeline.body = SAMPLE.read_bytes()[:500]  # cut off mid-record

    outcome = await pipeline.sync()

    assert outcome is SyncOutcome.UNAVAILABLE
    assert len(await pipeline.rig.store.load_current()) == 24
    assert len(pipeline.cache) == 24
    assert [name for name, _ in pipeline.alerts.alerts] == ["instrument_master_unavailable"]


async def test_a_master_that_lost_most_of_its_rows_is_rejected_and_yesterdays_kept(
    pipeline: Pipeline,
) -> None:
    await pipeline.sync()
    cash = [
        r for r in sample_rows() if r["exch_seg"] in ("NSE", "BSE") and r["instrumenttype"] == ""
    ]
    pipeline.serve_rows(cash[:6])  # valid JSON, valid rows — but a fraction of yesterday's

    outcome = await pipeline.sync()

    assert outcome is SyncOutcome.REJECTED
    assert len(await pipeline.rig.store.load_current()) == 24
    assert [name for name, _ in pipeline.alerts.alerts] == ["instrument_master_rejected"]


async def test_history_is_written_only_when_a_tracked_field_actually_changes(
    pipeline: Pipeline,
) -> None:
    await pipeline.sync()
    rows = sample_rows()
    for row in rows:
        row["strike"] = "-2.000000"  # an untracked field changing is not an instrument change
    pipeline.serve_rows(rows)
    assert await pipeline.sync() is SyncOutcome.NO_CHANGE
    assert await pipeline.rig.versions.count() == 0

    renamed = next(r for r in rows if r["token"] == "10099" and r["exch_seg"] == "NSE")
    renamed["symbol"] = "GODREJCP-BE"
    pipeline.serve_rows(rows)
    assert await pipeline.sync() is SyncOutcome.APPLIED

    (superseded,) = await pipeline.rig.versions.find({})
    assert (superseded.instrument_id, superseded.tradingsymbol) == ("NSE:10099", "GODREJCP-EQ")
    assert pipeline.cache.by_symbol(Exchange.NSE, "GODREJCP-BE").token == "10099"

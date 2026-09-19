from datetime import UTC, datetime
from typing import Any

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.instruments import Exchange
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiffer
from emporos.instruments.errors import MasterDownloadError, MasterFormatError
from emporos.instruments.sync import InstrumentSyncService, SyncOutcome
from emporos.instruments.validator import InstrumentMasterValidator
from tests.support.fakes import (
    InMemoryInstrumentMasterStore,
    RecordingAlertSink,
    StubMasterSource,
)
from tests.support.instrument_rows import as_master, cash_rows, instrument

NOW = datetime(2026, 3, 2, 3, 30, tzinfo=UTC)


class Rig:
    def __init__(self, source: StubMasterSource, store: InMemoryInstrumentMasterStore) -> None:
        self.store = store
        self.cache = InstrumentCache()
        self.alerts = RecordingAlertSink()
        self.service = InstrumentSyncService(
            source=source,
            validator=InstrumentMasterValidator(),
            differ=InstrumentDiffer(),
            store=store,
            cache=self.cache,
            alerts=self.alerts,
            clock=FixedClock(NOW),
        )


def _rig(rows: list[dict[str, Any]], store: InMemoryInstrumentMasterStore | None = None) -> Rig:
    return Rig(StubMasterSource(as_master(rows)), store or InMemoryInstrumentMasterStore())


def _yesterdays_master(count: int = 1200) -> InMemoryInstrumentMasterStore:
    return InMemoryInstrumentMasterStore([instrument(str(1000 + i)) for i in range(count)])


async def test_the_first_sync_loads_the_master_and_populates_the_cache() -> None:
    rig = _rig(cash_rows(1200))

    result = await rig.service.run()

    assert result.outcome is SyncOutcome.APPLIED and result.succeeded
    assert "1200 added" in result.message
    assert len(await rig.store.load_current()) == 1200
    assert rig.cache.by_token(Exchange.NSE, "1000").tradingsymbol == "SYM1000-EQ"
    assert rig.alerts.alerts == []


async def test_a_second_identical_sync_is_a_no_op() -> None:
    rig = _rig(cash_rows(1200), _yesterdays_master())

    result = await rig.service.run()

    assert result.outcome is SyncOutcome.NO_CHANGE and result.succeeded
    assert rig.store.apply_calls == 0
    assert len(rig.cache) == 1200


async def test_a_changed_instrument_is_applied_and_the_cache_refreshed() -> None:
    rows = cash_rows(1200)
    rows[3]["symbol"] = "RENAMED-EQ"
    rig = _rig(rows, _yesterdays_master())

    result = await rig.service.run()

    assert result.outcome is SyncOutcome.APPLIED
    assert "1 changed" in result.message
    assert rig.cache.by_symbol(Exchange.NSE, "RENAMED-EQ").token == "1003"


async def test_a_truncated_master_is_rejected_yesterdays_data_is_kept_and_an_alert_is_raised() -> (
    None
):
    store = _yesterdays_master()
    rig = _rig(cash_rows(300), store)

    result = await rig.service.run()

    assert result.outcome is SyncOutcome.REJECTED and not result.succeeded
    assert store.apply_calls == 0
    assert len(await store.load_current()) == 1200
    assert len(rig.cache) == 1200  # strategies keep resolving against yesterday's master
    (alert,) = rig.alerts.alerts
    assert alert[0] == "instrument_master_rejected"
    assert "only 300 cash rows" in alert[1]


@pytest.mark.parametrize("error", [MasterDownloadError("boom"), MasterFormatError("bad json")])
async def test_an_unavailable_or_unreadable_upstream_keeps_the_current_master(
    error: Exception,
) -> None:
    store = _yesterdays_master()
    rig = Rig(StubMasterSource(error=error), store)

    result = await rig.service.run()

    assert result.outcome is SyncOutcome.UNAVAILABLE and not result.succeeded
    assert store.apply_calls == 0
    assert len(rig.cache) == 1200
    assert [name for name, _ in rig.alerts.alerts] == ["instrument_master_unavailable"]

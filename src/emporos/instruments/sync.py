"""The daily instrument-master sync: download → validate → diff → atomic swap → cache refresh.

Stale instruments are far safer than none (plan.md §8), so any failure before
the swap leaves yesterday's master and the cache untouched, raises an alert, and
returns a non-success outcome for the scheduler to act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiff, InstrumentDiffer
from emporos.instruments.downloader import DownloadedMaster
from emporos.instruments.errors import MasterDownloadError, MasterFormatError, MasterRejectedError
from emporos.instruments.store import InstrumentMasterStore
from emporos.instruments.validator import InstrumentMasterValidator


class MasterSource(Protocol):
    async def download(self) -> DownloadedMaster: ...


class SyncOutcome(StrEnum):
    APPLIED = "applied"
    NO_CHANGE = "no_change"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SyncResult:
    outcome: SyncOutcome
    message: str

    @property
    def succeeded(self) -> bool:
        return self.outcome in (SyncOutcome.APPLIED, SyncOutcome.NO_CHANGE)


class InstrumentSyncService:
    def __init__(
        self,
        source: MasterSource,
        validator: InstrumentMasterValidator,
        differ: InstrumentDiffer,
        store: InstrumentMasterStore,
        cache: InstrumentCache,
        alerts: AlertSink,
        clock: Clock,
    ) -> None:
        self._source = source
        self._validator = validator
        self._differ = differ
        self._store = store
        self._cache = cache
        self._alerts = alerts
        self._clock = clock

    async def run(self) -> SyncResult:
        current = await self._store.load_current()
        self._cache.refresh(current)
        try:
            master = await self._source.download()
            validated = self._validator.validate(master, current_count=len(current))
        except MasterRejectedError as error:
            return self._fail(SyncOutcome.REJECTED, "instrument_master_rejected", str(error))
        except (MasterDownloadError, MasterFormatError) as error:
            return self._fail(
                SyncOutcome.UNAVAILABLE, "instrument_master_unavailable", error.message
            )

        diff = self._differ.diff(current, validated.instruments)
        if diff.is_empty:
            return SyncResult(SyncOutcome.NO_CHANGE, f"no change ({len(current)} instruments)")
        await self._store.apply(diff, self._clock.now())
        self._cache.refresh(validated.instruments)
        return SyncResult(SyncOutcome.APPLIED, self._applied_message(diff, validated.dropped_rows))

    def _fail(self, outcome: SyncOutcome, alert: str, message: str) -> SyncResult:
        self._alerts.raise_alert(alert, f"{message}; keeping the current instrument master")
        return SyncResult(outcome, f"{message}; kept the current master")

    @staticmethod
    def _applied_message(diff: InstrumentDiff, dropped_rows: int) -> str:
        return f"{diff.summary()} ({dropped_rows} unusable upstream rows skipped)"

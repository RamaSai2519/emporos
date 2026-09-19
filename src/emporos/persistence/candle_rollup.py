"""Nightly rollup: hot Mongo -> cold S3 Parquet (EM-58, plan.md §7 retention tiers).

For every timeframe with a finite hot retention, bars older than its cutoff are archived to S3,
VERIFIED by reading them back from the archive, and only then deleted from Mongo — by exact
timestamp, so a bar written in the meantime can never be removed. Ordering makes it crash-safe:

* die after archiving but before deleting: the next run re-archives (a merge, so a no-op) and
  finishes the delete — `CandleRepository` unions both tiers, so readers see one unchanged series;
* the archive fails or returns fewer bars than were written: nothing is deleted and the error
  surfaces, so a bad archive can never cost data.

Reads through `CandleRepository` are identical before and after a rollup.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.errors import DefinitiveError
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ColdCandleArchive
from emporos.persistence.placement import PlacementPolicy

_LOG = logging.getLogger(__name__)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DELETE_BATCH = 500
_ONE_SECOND = timedelta(seconds=1)


class RollableHotStore(Protocol):
    """The slice of the hot tier the rollup needs."""

    async def instruments_before(self, timeframe: Timeframe, cutoff: datetime) -> list[str]: ...

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...

    async def delete(
        self, instrument_id: str, timeframe: Timeframe, timestamps: Sequence[datetime]
    ) -> int: ...


class RollupVerificationError(DefinitiveError):
    """The archive did not hold what was just written. Nothing was deleted from the hot tier."""


@dataclass
class RollupReport:
    archived: int = 0
    deleted: int = 0
    instruments: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


class CandleRollup:
    def __init__(
        self,
        hot: RollableHotStore,
        cold: ColdCandleArchive,
        placement: PlacementPolicy,
        alerts: AlertSink | None = None,
    ) -> None:
        self._hot = hot
        self._cold = cold
        self._placement = placement
        self._alerts = alerts

    async def run(
        self,
        timeframes: Sequence[Timeframe] | None = None,
        instrument_ids: Sequence[str] | None = None,
    ) -> RollupReport:
        """Roll up every timeframe (or just `timeframes`) for every instrument holding aged bars
        (or just `instrument_ids`)."""
        report = RollupReport()
        for timeframe in timeframes or list(Timeframe):
            cutoff = self._placement.hot_cutoff(timeframe)
            if cutoff is None:  # this timeframe never leaves the hot tier
                continue
            for instrument_id in await self._hot.instruments_before(timeframe, cutoff):
                if instrument_ids is not None and instrument_id not in instrument_ids:
                    continue
                await self._roll(instrument_id, timeframe, cutoff, report)
        if report.failures and self._alerts is not None:
            self._alerts.raise_alert(
                "persistence.rollup_failed", f"{len(report.failures)} rollup(s) failed"
            )
        return report

    async def _roll(
        self, instrument_id: str, timeframe: Timeframe, cutoff: datetime, report: RollupReport
    ) -> None:
        old = await self._hot.read(instrument_id, timeframe, _EPOCH, cutoff)
        if not old:
            return
        try:
            await self._cold.archive(old)
            await self._verify(old)
        except Exception as error:
            _LOG.exception("rollup of %s/%s failed", instrument_id, timeframe.value)
            report.failures.append(f"{instrument_id}/{timeframe.value}: {type(error).__name__}")
            return
        deleted = 0
        stamps = [c.ts for c in old]
        for start in range(0, len(stamps), _DELETE_BATCH):
            deleted += await self._hot.delete(
                instrument_id, timeframe, stamps[start : start + _DELETE_BATCH]
            )
        report.archived += len(old)
        report.deleted += deleted
        report.instruments += 1

    async def _verify(self, written: Sequence[Candle]) -> None:
        first, last = written[0], written[-1]
        stored = {
            c.ts: c
            for c in await self._cold.read(
                first.instrument_id, first.timeframe, first.ts, last.ts + _ONE_SECOND
            )
        }
        missing = [c.ts for c in written if stored.get(c.ts) != c]
        if missing:
            raise RollupVerificationError(
                f"{len(missing)} of {len(written)} archived bars did not read back identically"
            )

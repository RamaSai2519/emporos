"""Reconnect recovery (EM-54, plan.md §7): after the feed drops and returns, backfill the
disconnected window from broker history and upsert it.

Sequence: (1) the subscription manager has already resubscribed (it is registered on the feed
client before this listener); (2) once the reconnect minute's own bar has closed and been flushed,
(3) fetch 1m bars for the gap from broker history, flag them `partial` (they cover a gap — plan
§7), upsert them, then rebuild the closed 5m/15m/1h bars the gap touched. Waiting for step (2)
matters: the live aggregator's bar for the reconnect minute is half a minute of data, and the
broker's is the complete one, so the broker's must be the LAST writer.

Backfill runs as a background task and never delays reconnection. One instrument failing does
not stop the rest; failures alert and the gap remains flagged partial on the live bars.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import IST, Clock, Sleeper
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.marketdata.aggregator import floor_minute
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.session import SessionWindow
from emporos.marketdata.timeframes import BucketRule, derive
from emporos.persistence.candles import CandleWriter

_LOG = logging.getLogger(__name__)
_MINUTE = timedelta(minutes=1)


class CandleBackfillSource(Protocol):
    """1m bars for an instrument over `[start, end)`, from broker history."""

    async def fetch_minutes(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[Candle]: ...


class ActiveInstruments(Protocol):
    @property
    def active(self) -> tuple[Instrument, ...]: ...


@dataclass(frozen=True)
class RecoveryReport:
    window_start: datetime
    window_end: datetime
    recovered: tuple[str, ...]
    failed: tuple[str, ...]
    candles_written: int


class ReconnectRecovery:
    """A `ConnectionListener`. Register it on the feed client AFTER the subscription manager."""

    def __init__(
        self,
        instruments: ActiveInstruments,
        source: CandleBackfillSource,
        writer: CandleWriter,
        clock: Clock,
        sleeper: Sleeper,
        grace: timedelta,
        persister: CandlePersister | None = None,
        window: SessionWindow | None = None,
        alerts: AlertSink | None = None,
    ) -> None:
        self._instruments = instruments
        self._source = source
        self._writer = writer
        self._clock = clock
        self._sleeper = sleeper
        self._grace = grace
        self._persister = persister
        self._window = window or SessionWindow()
        self._alerts = alerts
        self._gap_started: datetime | None = None
        self._tasks: set[asyncio.Task[RecoveryReport | None]] = set()
        self.reports: list[RecoveryReport] = []

    async def on_disconnected(self, reason: str) -> None:
        if self._gap_started is None:
            self._gap_started = self._clock.now()

    async def on_connected(self) -> None:
        if self._gap_started is None:
            return  # the first connection: there is no gap to repair
        gap_start, self._gap_started = self._gap_started, None
        task = asyncio.create_task(self._recover(gap_start, self._clock.now()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait(self) -> None:
        """Wait for any recovery in flight (for shutdown and tests)."""
        for task in list(self._tasks):
            with contextlib.suppress(Exception):
                await task

    async def _recover(self, gap_start: datetime, reconnected: datetime) -> RecoveryReport | None:
        window = self._gap_window(gap_start, reconnected)
        if window is None:
            return None  # the outage fell outside the session
        start, end = window
        await self._sleeper.sleep(self._seconds_until_reconnect_minute_is_settled(reconnected))
        if self._persister is not None:
            await self._persister.flush()  # so the broker's bars are written last
        recovered: list[str] = []
        failed: list[str] = []
        written = 0
        for instrument in self._instruments.active:
            try:
                written += await self._backfill(instrument, start, end)
                recovered.append(instrument.instrument_id)
            except Exception:
                failed.append(instrument.instrument_id)
                _LOG.exception("gap backfill failed for %s", instrument.instrument_id)
        if failed:
            self._alert(
                "market_data.backfill_failed",
                f"gap backfill failed for {len(failed)} instrument(s)",
            )
        report = RecoveryReport(start, end, tuple(recovered), tuple(failed), written)
        self.reports.append(report)
        return report

    async def _backfill(self, instrument: Instrument, start: datetime, end: datetime) -> int:
        """Write the gap's 1m bars (flagged partial) and every CLOSED higher-timeframe bar the gap
        touched. Higher bars are derived from whole buckets, fetched as context: deriving from the
        gap minutes alone would overwrite a straddling bucket with an incomplete bar."""
        rule = BucketRule(self._window)
        wide_start = rule.start(start, Timeframe.H1)
        wide_end = rule.end(rule.start(end - _MINUTE, Timeframe.H1), Timeframe.H1)
        fetched = await self._source.fetch_minutes(instrument, wide_start, wide_end)
        gap = [replace(bar, partial=True) for bar in fetched if start <= bar.ts < end]
        if not gap:
            return 0
        context = [bar for bar in fetched if not start <= bar.ts < end]
        higher = [
            bar
            for bar in derive([*gap, *context], window=self._window)
            if rule.end(bar.ts, bar.timeframe) <= end
            and bar.ts < end  # closed, and touched by the gap
            and rule.end(bar.ts, bar.timeframe) > start
        ]
        await self._writer.upsert([*gap, *higher])
        return len(gap) + len(higher)

    def _gap_window(
        self, gap_start: datetime, reconnected: datetime
    ) -> tuple[datetime, datetime] | None:
        """The session minutes the outage touched, `[start, end)`; None if it missed the session."""
        day = reconnected.astimezone(IST).date()
        opened, closed = self._window.open_at(day), self._window.close_at(day)
        start = max(floor_minute(gap_start), opened)
        end = min(floor_minute(reconnected) + _MINUTE, closed)
        return (start, end) if start < end else None

    def _seconds_until_reconnect_minute_is_settled(self, reconnected: datetime) -> float:
        settled = floor_minute(reconnected) + _MINUTE + self._grace + timedelta(seconds=1)
        return max((settled - self._clock.now()).total_seconds(), 0.0)

    def _alert(self, name: str, message: str) -> None:
        if self._alerts is not None:
            self._alerts.raise_alert(name, message)

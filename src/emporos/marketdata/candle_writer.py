"""Persists closed candles ONLY through `CandleWriter` (`CandleRepository.upsert`) — the single
allowed path (plan.md §6). Candles are buffered on the synchronous dispatch path and flushed in
batches; because the upsert is idempotent (unique index on instrument/timeframe/ts), a flush that
failed part-way is simply retried and re-running a session's aggregation cannot create duplicates.
A failed flush keeps the buffer: a closed bar is never dropped because Mongo hiccuped.
"""

from __future__ import annotations

import logging

from emporos.core.alerts import AlertSink
from emporos.domain.candles import Candle
from emporos.persistence.candles import CandleWriter

_LOG = logging.getLogger(__name__)
DEFAULT_BATCH = 500


class CandlePersister:
    """A `CandleSubscriber` that batches candles and upserts them on `flush`."""

    def __init__(
        self, writer: CandleWriter, alerts: AlertSink | None = None, batch_size: int = DEFAULT_BATCH
    ) -> None:
        self._writer = writer
        self._alerts = alerts
        self._batch_size = batch_size
        self._pending: list[Candle] = []
        self._failures = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def failures(self) -> int:
        return self._failures

    def on_candle(self, candle: Candle) -> None:
        self._pending.append(candle)

    async def flush(self) -> int:
        """Upsert everything pending; returns how many were written. Never raises."""
        written = 0
        while self._pending:
            batch = self._pending[: self._batch_size]
            try:
                await self._writer.upsert(batch)
            except Exception:
                self._failures += 1
                _LOG.exception("candle upsert failed; %d candle(s) kept for retry", self.pending)
                if self._alerts is not None:
                    self._alerts.raise_alert(
                        "market_data.candle_write_failed",
                        f"{self.pending} closed candle(s) could not be persisted; will retry",
                    )
                return written
            del self._pending[: len(batch)]
            written += len(batch)
        return written

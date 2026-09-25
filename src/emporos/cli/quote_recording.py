"""Switching L1 quote recording on in a worker (EM-217, EDGE_SEARCH_PLAN D7).

A worker started with `--record-quotes` polls the D1 names' best bid, ask, sizes and last price once
a minute during the session and writes them to Parquet under `--quotes-dir`. It is one more
scheduled job in the worker's own loop over the worker's own broker session: read-only (the recorder
is handed the market-data calls only), never in Mongo, and it yields to any order call in flight."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from emporos.broker.paper.market import MarketDataOnly, MarketDataSource
from emporos.core.clock import Clock
from emporos.quotes.priority import OrderPriority
from emporos.quotes.recorder import QuoteRecorder, RecorderSettings
from emporos.quotes.sink import ParquetQuoteSink
from emporos.research.d1_universe import DEFAULT_MANIFEST, D1Manifest
from emporos.session.jobs import Job

__all__ = ["DEFAULT_QUOTES_DIR", "QuoteRecordingPlan", "d1_plan"]

DEFAULT_QUOTES_DIR = Path("data/quotes")


@dataclass(frozen=True)
class QuoteRecordingPlan:
    """What to record, where, and how often."""

    instrument_ids: tuple[str, ...]
    directory: Path
    settings: RecorderSettings = field(default_factory=RecorderSettings)

    def job(self, source: MarketDataSource, priority: OrderPriority, clock: Clock) -> Job:
        """The scheduler job. `source` is narrowed to the market-data calls before the recorder
        sees it, so nothing it holds can place an order."""
        recorder = QuoteRecorder(
            MarketDataOnly(source),
            ParquetQuoteSink(self.directory, clock),
            self.instrument_ids,
            self.settings,
            clock,
            priority=priority,
        )
        return Job("quote_recorder", self.settings.interval, recorder.poll)


def d1_plan(
    directory: Path = DEFAULT_QUOTES_DIR,
    interval_seconds: int = 60,
    manifest: Path = DEFAULT_MANIFEST,
) -> QuoteRecordingPlan:
    """The D1 universe: the included names AND the held-out ones. Recording is not reading: a
    forward quote file for a held-out name is new data no research has seen, and what research may
    read of it is still governed by the seeded holdout."""
    loaded = D1Manifest.load(manifest)
    ids = tuple(dict.fromkeys((*loaded.included, *loaded.holdout)))
    return QuoteRecordingPlan(
        ids, directory, RecorderSettings(interval=timedelta(seconds=interval_seconds))
    )

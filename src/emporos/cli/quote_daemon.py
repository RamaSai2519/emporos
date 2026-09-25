"""`emporos worker record-quotes`: a process that records L1 quotes and does nothing else (EM-236).

It logs in to Angel One for the quote endpoint and nothing more: no strategy, no risk engine, no
execution gateway, no order-update socket, no tick feed, no Mongo. The only broker call it can make
is `AngelOneQuoteSource.get_quote`, which has no order method to reach. Started by a systemd unit on
the production host each morning; on a weekend or an exchange holiday it writes nothing and exits 0;
at the close (or on SIGTERM) it writes the last rows, uploads the day's files and logs out."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.factory import AngelOneStack, AngelOneStackFactory
from emporos.broker.angelone.quote_source import AngelOneQuoteSource
from emporos.broker.angelone.transport import HttpClientFactory
from emporos.broker.backoff import JitterSource
from emporos.cli.option_master import ScripMasterContracts
from emporos.cli.quote_recording import OPTIONS_PREFIX, QuoteRecordingPlan
from emporos.core.clock import IST, Clock, Sleeper
from emporos.core.config import Settings
from emporos.persistence.object_store import S3ClientFactory, S3ObjectStore
from emporos.quotes.day import Companion, DayOutcome, QuoteRecordingDay
from emporos.quotes.option_recorder import ContractSource, OptionQuoteRecorder
from emporos.quotes.option_row import ParquetOptionSink
from emporos.quotes.recorder import QuoteRecorder
from emporos.quotes.sink import ParquetQuoteSink
from emporos.quotes.underlyings import INDEX_RULES, index_and_stock_rules
from emporos.quotes.upload import DayUpload, DayUploader, RemoteFiles
from emporos.quotes.window import RecordingWindow

__all__ = ["QuoteDaemon", "QuoteDaemonResult"]

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuoteDaemonResult:
    outcome: DayOutcome
    uploaded: int
    upload_failed: int

    @property
    def exit_code(self) -> int:
        return 2 if self.upload_failed else 0


class QuoteDaemon:
    def __init__(
        self,
        settings: Settings,
        plan: QuoteRecordingPlan,
        clock: Clock,
        sleeper: Sleeper,
        jitter: JitterSource,
        http_factory: HttpClientFactory | None = None,
        store: RemoteFiles | None = None,
        contracts: ContractSource | None = None,
    ) -> None:
        self._settings, self._plan = settings, plan
        self._http_factory, self._store, self._contracts = http_factory, store, contracts
        self._clock, self._sleeper, self._jitter = clock, sleeper, jitter

    async def run(self) -> QuoteDaemonResult:
        window = RecordingWindow()
        factory = AngelOneStackFactory(
            self._settings, self._clock, self._sleeper, self._jitter, self._http_factory
        )
        stack = factory.build()
        source = AngelOneQuoteSource(AngelOneApi(stack.transport))
        recorder = QuoteRecorder(
            source,
            ParquetQuoteSink(self._plan.directory, self._clock),
            self._plan.instrument_ids,
            self._plan.settings,
            self._clock,
            window,
        )
        day = QuoteRecordingDay(
            recorder, self._clock, self._sleeper, window, self._plan.settings.interval,
            companion=self._option_recorder(source, window),
        )  # fmt: skip
        task = asyncio.current_task()
        with self._stop_on_signal(task):
            try:
                outcome = await day.run()
            except asyncio.CancelledError:
                outcome = DayOutcome.INTERRUPTED  # rows are already written by the day
            finally:
                await self._close(stack)
        return await self._upload(outcome)

    def _option_recorder(
        self, source: AngelOneQuoteSource, window: RecordingWindow
    ) -> Companion | None:
        """The option quotes beside the stock quotes: the same session, the same quote calls."""
        options = self._plan.options
        if options is None:
            return None
        try:
            rules = index_and_stock_rules(options.underlyings)
        except (OSError, KeyError, TypeError, ValueError) as error:
            _LOG.error("option underlyings not read (%s): recording the indexes only", error)
            rules = INDEX_RULES
        contracts = self._contracts or ScripMasterContracts([r.underlying for r in rules])
        return OptionQuoteRecorder(
            source, ParquetOptionSink(options.directory, self._clock), contracts, rules,
            options.settings, self._clock, window,
        )  # fmt: skip

    @contextlib.contextmanager
    def _stop_on_signal(self, task: asyncio.Task[object] | None) -> Iterator[None]:
        loop = asyncio.get_running_loop()
        added: list[signal.Signals] = []
        if task is not None:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, task.cancel)
                added.append(sig)
        try:
            yield
        finally:
            for sig in added:
                loop.remove_signal_handler(sig)

    async def _close(self, stack: AngelOneStack) -> None:
        try:
            await stack.sessions.logout()
        except Exception as error:  # a failed logout must not lose the upload
            _LOG.warning("quote recorder logout failed: %s", error)
        finally:
            await stack.aclose()

    async def _upload(self, outcome: DayOutcome) -> QuoteDaemonResult:
        store = self._store or self._s3()
        if store is None:
            _LOG.warning("S3_BUCKET is not set: the day's quote files stay on this host")
            return QuoteDaemonResult(outcome, 0, 0)
        today = self._clock.now().astimezone(IST).date()
        report = await DayUploader(store, Path(self._plan.directory)).upload(today)
        _LOG.info("quote upload: %s", report)
        if self._plan.options is not None:
            options = await DayUploader(
                store, Path(self._plan.options.directory), OPTIONS_PREFIX
            ).upload(today)
            _LOG.info("option quote upload: %s", options)
            report = DayUpload(
                report.uploaded + options.uploaded,
                report.skipped + options.skipped,
                report.failed + options.failed,
            )
        return QuoteDaemonResult(outcome, report.uploaded, report.failed)

    def _s3(self) -> S3ObjectStore | None:
        if not self._settings.s3_bucket:
            return None
        return S3ObjectStore(S3ClientFactory(self._settings).create(), self._settings.s3_bucket)

"""EM-59: the Phase 6 suite's cross-cutting cases — the REAL transport stack absorbing the upstream
rate-limit defect during a backfill, and a watchlist-scale warm-up with zero gaps and duplicates."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import httpx

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.retry import RetryingTransport
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import HttpRestTransport, RestRequest
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.core.clock import FixedClock
from emporos.domain.candles import Timeframe
from emporos.history.backfill import BackfillOrchestrator
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.gaps import GapDetector
from emporos.history.grid import SessionGrid
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candles import CandleRepository
from tests.support.angelone_fixtures import FixtureLibrary
from tests.support.fakes import (
    AdvancingSleeper,
    FixedJitter,
    InMemoryCandleStore,
    InMemoryCoverageStore,
    InMemoryObjectStore,
    ScriptedHttpServer,
    make_instrument,
)
from tests.support.history import BrokerHistory

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
SBIN = make_instrument("3045", symbol="SBIN-EQ")
RECORDED = FixtureLibrary()
DENIAL = RECORDED.get("login_rate_limited")  # the real 403 plain-text denial, recorded live
CANDLES = RECORDED.get("candles_1m")
DAY = date(2026, 9, 18)  # the day the recorded candle fixture covers


class Authed:
    """Stands in for the session layer so the stack under test is retry -> limiter -> HTTP."""

    def __init__(self, inner: RetryingTransport) -> None:
        self._inner = inner

    async def send(self, request: RestRequest) -> Any:
        return await self._inner.send(replace(request, bearer="t"))


class Rig:
    def __init__(self, *replies: httpx.Response) -> None:
        self.server = ScriptedHttpServer().queue(*replies)
        clock = FixedClock(NOW)
        self.sleeper = AdvancingSleeper(clock)
        stack = RetryingTransport(
            RateLimitedTransport(
                HttpRestTransport(self.server.client(), "key"),
                GroupRateLimiter(ANGELONE_RATE_LIMITS, clock, self.sleeper),
            ),
            self.sleeper,
            FixedJitter(0.5),
        )
        self.store = InMemoryCandleStore()
        self.repo = CandleRepository(self.store, ParquetCandleArchive(InMemoryObjectStore()))
        self.coverage = InMemoryCoverageStore()
        self.orchestrator = BackfillOrchestrator(
            AngelOneCandleBackfill(AngelOneApi(Authed(stack))),
            self.repo,
            self.coverage,
            SessionGrid(StoredTradingCalendar()),
            clock,
        )


def sbin_bars_on_day() -> int:
    return len(CANDLES.data)


async def test_a_backfill_absorbs_a_burst_of_real_rate_limit_denials_via_backoff() -> None:
    rig = Rig(DENIAL.response(), DENIAL.response(), DENIAL.response(), CANDLES.response())

    report = await rig.orchestrator.run([SBIN], DAY, DAY)

    assert report.ok and report.chunks_fetched == 1
    assert len(rig.server.requests) == 4  # three denials, one success: nobody special-cased it
    assert len([s for s in rig.sleeper.sleeps if s >= 0.5]) >= 3  # it really backed off
    stored = await rig.store.read(
        SBIN.instrument_id, Timeframe.M1, datetime(2026, 9, 1, tzinfo=UTC), NOW
    )
    assert len(stored) == sbin_bars_on_day()


async def test_a_denial_that_never_clears_fails_the_chunk_without_marking_it_covered() -> None:
    rig = Rig(*[DENIAL.response() for _ in range(8)])  # the whole retry budget, all denied

    report = await rig.orchestrator.run([SBIN], DAY, DAY)

    assert not report.ok and report.failed_chunks == [(SBIN.instrument_id, DAY, DAY)]
    assert (
        await rig.coverage.get_days(SBIN.instrument_id, Timeframe.M1, DAY, DAY) == {}
    )  # not covered

    rig.server.queue(CANDLES.response())  # the defect clears; the next run picks the day up
    retry = await rig.orchestrator.run([SBIN], DAY, DAY)
    assert retry.ok and retry.chunks_fetched == 1


async def test_the_recorded_broker_response_is_covered_and_its_omitted_minutes_are_learned() -> (
    None
):
    rig = Rig(CANDLES.response())
    await rig.orchestrator.run([SBIN], DAY, DAY)
    grid = SessionGrid(StoredTradingCalendar())

    gaps = await GapDetector(rig.repo, rig.coverage, grid).find(SBIN.instrument_id, DAY, DAY)

    assert gaps == []  # 5 real bars stored; the other 370 minutes are recorded as broker-absent
    (coverage,) = (await rig.coverage.get_days(SBIN.instrument_id, Timeframe.M1, DAY, DAY)).values()
    assert coverage.complete and not coverage.empty


async def test_a_watchlist_warm_up_completes_with_zero_gaps_and_zero_duplicates() -> None:
    watchlist = [make_instrument(str(1000 + n)) for n in range(8)]
    first, last = date(2026, 8, 10), date(2026, 9, 11)  # 25 trading days of 1m indicator warm-up
    history = BrokerHistory(silent=lambda ts: ts.minute in (7, 8))  # the broker omits some minutes
    store = InMemoryCandleStore()
    repo = CandleRepository(store, ParquetCandleArchive(InMemoryObjectStore()))
    coverage = InMemoryCoverageStore()
    grid = SessionGrid(StoredTradingCalendar())
    orchestrator = BackfillOrchestrator(history, repo, coverage, grid, FixedClock(NOW))

    report = await orchestrator.run(watchlist, first, last)
    rerun = await orchestrator.run(watchlist, first, last)

    assert report.ok and rerun.chunks_fetched == 0 and rerun.days_already_covered == 8 * 25
    detector = GapDetector(repo, coverage, grid)
    for instrument in watchlist:
        assert await detector.find(instrument.instrument_id, first, last) == []  # zero gaps
        series = await repo.get_range(
            instrument.instrument_id, Timeframe.M1, datetime(2026, 8, 1, tzinfo=UTC), NOW
        )
        assert len({b.ts for b in series}) == len(series) == 25 * (375 - 2 * 6)  # zero duplicates

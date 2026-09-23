"""EM-108: BacktestJob — universe as of the first day, config resolved against it, the window,
and the assumptions it records."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.backtest.integrity import ResearchIntegrityViolation
from emporos.backtest.job import BacktestJob, BacktestRequest, ResolverTickSizes
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.cli.strategy_composition import build_registry
from emporos.domain.instruments import (
    Exchange,
    Instrument,
    InstrumentResolver,
    UnknownInstrumentError,
)
from emporos.domain.money import Money
from emporos.history.quarantine import CorporateActionQuarantine, QuarantineEntry, QuarantineSource
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigError, StrategyConfigResolver
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import FixedSchedule
from tests.support.backtest_momentum import DAYS, momentum_backtest, momentum_candles
from tests.support.strategies import INSTRUMENT_MASTER, momentum_raw

FIRST = date(2026, 1, 5)  # a Monday
LAST = FIRST + timedelta(days=DAYS - 1)


def instrument(token: str, symbol: str) -> Instrument:
    return Instrument(Exchange.NSE, token, symbol, symbol, 1, Money.of("0.05"))


def eras(valid_from: datetime) -> AsOfInstruments:
    return AsOfInstruments(
        [
            InstrumentEra(instrument("1001", "ALPHA-EQ"), valid_from, None),
            InstrumentEra(instrument("1002", "BETA-EQ"), valid_from, None),
        ]
    )


def config_for(resolver: InstrumentResolver) -> ResolvedStrategyConfig:
    return StrategyConfigResolver(build_registry(), resolver).resolve(momentum_raw())


def job(history_from: datetime) -> BacktestJob:
    return BacktestJob(
        InMemoryCandles(momentum_candles()),
        build_registry(),
        eras(history_from),
        config_for,
        lambda: FixedSchedule(),
    )


def request(**kwargs: object) -> BacktestRequest:
    return BacktestRequest(FIRST, LAST, Money.of("1000000"), **kwargs)  # type: ignore[arg-type]


LONG_AGO = datetime(2025, 1, 1, tzinfo=UTC)


async def test_the_job_gives_the_same_result_as_running_the_engine_directly() -> None:
    direct = await momentum_backtest()

    via_job = await job(LONG_AGO).run(request())

    assert via_job.metrics.ending_equity == direct.metrics.ending_equity
    assert len(via_job.trades) == len(direct.trades) > 0
    assert via_job.counters == direct.counters


async def test_the_window_is_midnight_ist_of_the_first_day_to_midnight_ist_after_the_last() -> None:
    result = await job(LONG_AGO).run(request())

    window = result.spec.window
    assert window.start == datetime(2026, 1, 4, 18, 30, tzinfo=UTC)  # 00:00 IST on the 5th
    assert window.end == datetime(2026, 1, 10, 18, 30, tzinfo=UTC)  # 00:00 IST on the 11th


async def test_a_universe_with_history_is_resolved_as_of_the_first_day_and_says_so() -> None:
    result = await job(LONG_AGO).run(request())

    (note,) = result.spec.assumptions
    assert "resolved from the instrument master as it stood on 2026-01-05" in note


async def test_without_history_for_the_day_the_config_fails_loudly_unless_assumed() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)  # the master only began recording in June

    with pytest.raises((StrategyConfigError, UnknownInstrumentError)):
        await job(later).run(request())

    result = await job(later).run(request(assume_current_universe=True))
    (note,) = result.spec.assumptions
    assert "2 of 2 instrument(s)" in note and "EARLIEST recorded definition" in note
    assert "2026-01-05" in note and "EM-99 H8" in note


async def test_the_tick_size_comes_from_the_same_universe() -> None:
    universe = eras(LONG_AGO).as_of(datetime(2026, 1, 5, tzinfo=UTC)).resolver
    assert ResolverTickSizes(universe).tick_size("NSE:1001") == Money.of("0.05")
    assert ResolverTickSizes(INSTRUMENT_MASTER).tick_size("NSE:1002") == Money.of("0.05")
    with pytest.raises(UnknownInstrumentError):
        ResolverTickSizes(universe).tick_size("NSE:9999")


async def test_the_result_carries_provenance_for_the_universe_it_ran_against() -> None:
    result = await job(LONG_AGO).run(request())

    provenance = result.spec.provenance
    assert provenance is not None
    assert provenance.dataset_first == FIRST and provenance.dataset_last == LAST
    assert provenance.universe_hash.startswith("sha256:")
    assert provenance.assumed_instrument_ids == ()


async def test_a_quarantined_day_inside_the_window_refuses_the_run_unless_opted_in() -> None:
    entry = QuarantineEntry(
        "NSE:1001", FIRST + timedelta(days=1), "split", QuarantineSource.DETECTED, LONG_AGO
    )
    quarantine = CorporateActionQuarantine([entry])
    clean = await job(LONG_AGO).run(request())
    gated = BacktestJob(
        InMemoryCandles(momentum_candles()), build_registry(), eras(LONG_AGO), config_for,
        lambda: FixedSchedule(), quarantine=quarantine,
    )  # fmt: skip

    with pytest.raises(ResearchIntegrityViolation, match="NSE:1001"):
        await gated.run(request())

    allowed = await gated.run(request(allow_quarantined_instruments=True))
    assert allowed.spec.provenance is not None and clean.spec.provenance is not None
    assert allowed.spec.provenance.quarantine_hash != clean.spec.provenance.quarantine_hash


def test_a_request_runs_forward_in_time() -> None:
    with pytest.raises(ValueError):
        BacktestRequest(LAST, FIRST, Money.of("1"))
    assert BacktestRequest(FIRST, FIRST, Money.of("1")).first_day == FIRST

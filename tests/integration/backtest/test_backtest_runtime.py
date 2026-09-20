"""EM-108: the Atlas read path of `emporos backtest`, against the real shared dev database.

READ-ONLY: it reads candles and the instrument master; it writes nothing and needs no broker."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.document import BacktestDocument
from emporos.backtest.job import BacktestRequest
from emporos.backtest.universe import AsOfInstruments
from emporos.cli.backtest_runtime import InstrumentErasReader, run_backtest
from emporos.core.config import Settings
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.session.strategy_files import STRATEGY_CONFIG_DIR
from tests.support.backtest_real import STARTING_CASH, real_year_backtest

pytestmark = pytest.mark.integration

WINDOW = BacktestRequest(
    date(2026, 3, 9), date(2026, 3, 13), STARTING_CASH, assume_current_universe=True
)


async def test_the_instrument_history_reads_back_as_eras(
    database: AsyncDatabase[Mapping[str, Any]],
) -> None:
    eras = await InstrumentErasReader(database).read()

    current = {e.instrument.tradingsymbol: e for e in eras if e.valid_to is None}
    assert {"RELIANCE-EQ", "TCS-EQ"} <= set(current)
    assert current["RELIANCE-EQ"].instrument.instrument_id == "NSE:2885"
    assert all(e.valid_to is None or e.valid_to > e.valid_from for e in eras)
    today = AsOfInstruments(eras).as_of(datetime.now(UTC))
    assert today.resolver.by_symbol(Exchange.NSE, "RELIANCE-EQ").token == "2885"


async def test_a_backtest_read_from_atlas_equals_the_same_backtest_on_the_committed_fixture(
    dev_settings: Settings,
) -> None:
    """The plumbing check: CandleRepository over Atlas, warm-up bars, chunked reads, the instrument
    eras and the fee schedule together give exactly what the frozen data gives. It skips if Atlas
    no longer holds this window (the fixture, not Atlas, is what the goldens rest on)."""
    from_atlas = await run_backtest(
        dev_settings, STRATEGY_CONFIG_DIR / "momentum_v1.yaml", WINDOW, assume_earliest_fees=True
    )
    if from_atlas.metrics.trading_days == 0:
        pytest.skip("Atlas holds no bars for this window any more")

    from_fixture = await real_year_backtest(WINDOW)

    assert BacktestDocument().render(from_atlas) == BacktestDocument().render(from_fixture)
    assert from_atlas.metrics.starting_cash == Money.of("100000")

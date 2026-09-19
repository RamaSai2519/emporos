"""EM-65: a strategy runs a full paper session on real Atlas — signals, orders, fills, positions
and P&L all persisted through the real repositories — and the session is then reconstructed from
persisted state alone.

The tape is synthetic because the market is closed while this is written; the same run over live
NSE ticks is tracked in EM-99. Nothing here can reach a broker: the market-data source has no
order methods and the paper broker holds no credentials."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from emporos.broker.models import MarketDataMode
from emporos.broker.paper.costs import ScheduledCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.fills import ParticipationLiquidity
from emporos.cli.paper_composition import PaperComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.fees import IntradayCharges
from emporos.domain.money import Money
from emporos.persistence.collections import Collection
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import SignalRecord
from emporos.persistence.repositories import SignalRepository
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_mongo import PaperMongoRig, assert_session_reconstructs
from tests.support.paper_rig import ID, SBIN
from tests.support.paper_strategy import DipScalp, Intent, Signal, StrategyDriver

pytestmark = pytest.mark.integration
DAY = "2026-09-18"
CASH = Money.of("1000000")


class MongoSignals:
    """Persists each signal through `SignalRepository` — what the strategy engine will do."""

    def __init__(self, repository: SignalRepository, run_id: str, ids: IdGenerator) -> None:
        self._repository, self._run_id, self._ids = repository, run_id, ids

    async def record(self, signal: Signal) -> None:
        await self._repository.insert(
            SignalRecord(
                _id=f"sig-{self._ids.new_ulid()}",
                strategy_run_id=self._run_id,
                instrument_id=signal.instrument_id,
                ts=signal.at,
                kind=signal.kind.value,
                price=signal.price,
                quantity=signal.quantity,
                ordertag=signal.ordertag,
            )
        )


@pytest.fixture
async def rig(dev_settings: Any) -> AsyncIterator[PaperMongoRig]:
    mongo = MongoClientFactory(dev_settings)
    r = PaperMongoRig(mongo.client, mongo.database())
    await r.prepare()
    try:
        yield r
    finally:
        await r.cleanup()
        await mongo.close()


async def test_a_strategy_runs_a_full_session_and_it_reconstructs_from_persisted_state(
    rig: PaperMongoRig,
) -> None:
    ids, clock, market = IdGenerator(), FixedClock(NOW), FakeMarketData([SBIN], [])
    signals_repo = SignalRepository(rig.database)
    run_id = f"run-{ids.new_ulid()}"
    schedule = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21))
    runtime = await PaperComposer(
        source=market,
        factory=PaperBrokerFactory(
            PaperBrokerConfig(rig.account_id, CASH),
            ScheduledCosts(IntradayCharges(schedule)),
            liquidity=ParticipationLiquidity(Decimal("0.5")),
        ),
        journal=rig.journal(),
        store=rig.store,
        client_code=rig.account_id,
        clock=clock,
        ids=ids,
        sleeper=AsyncioSleeper(),
        alerts=LogAlertSink(),
        flush_interval_seconds=0.05,
    ).open()
    flusher = asyncio.create_task(runtime.flusher.run())  # keeps the write-behind journal durable
    try:
        broker = runtime.broker
        await broker.subscribe_market_data([ID], MarketDataMode.QUOTE)
        strategy = DipScalp(
            ID, Money.of("99.80"), Money.of("100.30"), Money.of("99.30"), Money.of("99.25"), 100
        )
        driver = StrategyDriver(
            broker, strategy, MongoSignals(signals_repo, run_id, ids), ids, market.emit
        )

        def tape(*moves: tuple[str, int]):  # type: ignore[no-untyped-def]
            volume = 5_000_000
            for n, (price, shares) in enumerate(moves, start=1):
                clock.advance(timedelta(seconds=1))
                volume += shares
                yield make_tick(clock.now(), price, volume=volume, instrument_id=ID, sequence=n)

        await driver.run(
            tape(
                ("100.00", 0),
                ("99.80", 50),
                ("99.80", 400),
                ("100.10", 300),
                ("100.30", 400),
                ("100.50", 100),
            )  # fmt: skip
        )
    finally:
        flusher.cancel()
        await asyncio.gather(flusher, return_exceptions=True)  # cancelling flushes once more

    try:
        assert strategy.done
        # signals -> orders: every signal names the order it produced (or cancelled)
        signals = await signals_repo.for_run(run_id)
        assert [s.kind for s in signals] == [
            Intent.ENTER, Intent.TAKE_PROFIT, Intent.STOP_LOSS, Intent.CANCEL_STOP,
        ]  # fmt: skip
        tags = {o.ordertag: o for o in await rig.orders.for_account_session(rig.account_id, DAY)}
        assert all(s.ordertag in tags for s in signals) and len(tags) == 3
        assert sorted(o.state for o in tags.values()) == ["CANCELLED", "FILLED", "FILLED"]
        # fills, position and P&L
        executions = await rig.executions.for_account_session(rig.account_id, DAY)
        assert [(x.quantity, x.session_trade_no) for x in executions] == [(100, 1), (100, 2)]
        (position,) = await rig.positions.find({"account_id": rig.account_id})
        fees = executions[0].fees, executions[1].fees
        assert position.net_quantity == 0 and fees[0] is not None and fees[1] is not None
        assert position.realised_pnl == Money.of("50.00") - fees[0] - fees[1]
        snapshots = await rig.snapshots.find({"account_id": rig.account_id})
        assert len(snapshots) == 2
        assert max(snapshots, key=lambda s: s.trades or 0).cash == CASH + position.realised_pnl
        # and the whole session rebuilds from persisted state alone
        await assert_session_reconstructs(rig, runtime.broker, DAY, CASH)
    finally:
        await rig.database[Collection.SIGNALS].delete_many({"strategy_run_id": run_id})

"""EM-138 — `LivePaperWorker` runs a paper session over a live feed it is handed.

The worker, composer, risk, execution and Atlas are the production ones; the feed is scripted
(`tests/support/live_feed.py`) and the clock runs in virtual time. Nothing here can reach a broker.
What is asserted is the wiring the paper-on-live-data process adds: every loadable strategy's
universe is subscribed, a launched strategy is warmed up, the feed's lifetime brackets the
session, and a bad request fails before any feed is opened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from emporos.cli.live_paper_worker import LivePaperWorker, PaperWorkerOptions
from emporos.cli.worker_composition import WorkerTuning
from emporos.core.clock import FixedClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.core.ids import IdGenerator
from emporos.domain.instruments import Exchange
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.session.lifecycle import SessionState
from emporos.session.worker import SessionSchedule
from tests.support.live_feed import AdvancingSleeper, ScriptedFeedOpener, ScriptedWarmup
from tests.support.worker_rig import WorkerWorld, cleanup

pytestmark = pytest.mark.integration

# 15:10 IST on a Tuesday after the fee schedule takes effect: a short session in virtual time.
NEAR_CLOSE = datetime(2026, 9, 22, 9, 40, tzinfo=UTC)
DAY = "2026-09-22"
DAY_RANGE = {
    "$gte": datetime(2026, 9, 21, 18, 30, tzinfo=UTC),
    "$lt": datetime(2026, 9, 22, 18, 30, tzinfo=UTC),
}
STRATEGIES = {"orb_v1": "SBIN-EQ", "gap_go_v1": "INFY-EQ"}  # each config's universe, cut to one


class Rig:
    def __init__(self, world: WorkerWorld, settings: Settings, tmp_path: Path) -> None:
        self.world, self.settings, self.tmp_path = world, settings, tmp_path
        self.clock = FixedClock(NEAR_CLOSE)

    def files(self) -> list[Path]:
        """The shipped configs with their universe cut to one symbol, so the run stays small."""
        paths = []
        for name, symbol in STRATEGIES.items():
            document = yaml.safe_load(Path(f"config/strategies/{name}.yaml").read_text())
            document["universe"]["instruments"] = [f"NSE:{symbol}"]
            path = self.tmp_path / f"{name}.yaml"
            path.write_text(yaml.safe_dump(document))
            paths.append(path)
        return paths

    async def orders(self) -> list[Mapping[str, Any]]:
        db = self.world.mongo.database()
        mine = {"account_id": self.world.account_id}
        return [o async for o in db[Collection.ORDERS].find(mine)] + [
            o async for o in db[Collection.PAPER_ORDERS].find(mine)
        ]

    def worker(self, feeds: ScriptedFeedOpener, start: tuple[str, ...] = ()) -> LivePaperWorker:
        options = PaperWorkerOptions(
            self.files(), start, self.world.account_id,
            kill_switch_collection=self.world.kill_switch_collection,
            tuning=WorkerTuning(schedule=SessionSchedule(poll_interval=timedelta(seconds=30))),
        )  # fmt: skip
        return LivePaperWorker(
            self.settings, options, feeds, self.clock, AdvancingSleeper(self.clock)
        )


@pytest.fixture
async def rig(dev_settings: Settings, tmp_path: Path) -> AsyncIterator[Rig]:
    mongo = MongoClientFactory(dev_settings)
    suffix = IdGenerator().new_ulid().lower()
    world = WorkerWorld(
        mongo=mongo,
        client=mongo.client,
        account_id=f"LIVEPAPERIT{suffix.upper()}",
        sentinel_path=tmp_path / "HALT",
        kill_switch_collection=f"zz_kill_switch_{suffix}",
    )
    await MigrationRunner(MongoSchemaStore(mongo.database()), PLATFORM_SCHEMA).apply()
    settings = dev_settings.model_copy(update={"kill_switch_file": str(world.sentinel_path)})
    try:
        yield Rig(world, settings, tmp_path)
    finally:
        db = mongo.database()
        runs = [
            r["_id"]
            async for r in db[Collection.STRATEGY_RUNS].find(
                {"strategy_name": {"$in": list(STRATEGIES)}, "session_date": DAY}
            )
        ]
        await db[Collection.SIGNALS].delete_many({"strategy_run_id": {"$in": runs}})
        await db[Collection.RISK_EVENTS].delete_many({"strategy_run_id": {"$in": runs}})
        await db[Collection.STRATEGY_RUNS].delete_many({"_id": {"$in": runs}})
        await db[Collection.SYSTEM_EVENTS].delete_many(
            {"type": {"$in": ["session_state", "alert"]}, "ts": DAY_RANGE}
        )
        await db[Collection.RECONCILIATION_RUNS].delete_many({"ts": DAY_RANGE})
        await cleanup(world)
        await mongo.close()


class TestALiveSession:
    async def test_every_loadable_universe_is_subscribed_and_the_feed_brackets_the_session(
        self, rig: Rig
    ) -> None:
        feeds = ScriptedFeedOpener()

        report = await rig.worker(feeds).run()

        assert report.final_state is SessionState.SHUTTING_DOWN and report.failure == ""
        assert await rig.orders() == []  # no strategy was started, so nothing was placed
        assert feeds.market is not None and len(feeds.requests) == 1
        cache = feeds.requests[0].instruments
        expected = {cache.by_symbol(Exchange.NSE, s).instrument_id for s in STRATEGIES.values()}
        assert feeds.market.subscribed == expected  # so a dashboard START has live data at once
        assert feeds.warmup.requested == []  # nothing was launched, so nothing was warmed
        assert feeds.events == ["opened", "ticks on", "ticks off", "closed"]  # brackets the session

    async def test_a_strategy_started_at_launch_is_recorded_and_warmed_up(self, rig: Rig) -> None:
        warmup = ScriptedWarmup()
        feeds = ScriptedFeedOpener(warmup)

        report = await rig.worker(feeds, start=("orb_v1",)).run()

        assert report.failure == ""
        assert warmup.requested == ["orb_v1"]  # the other loadable strategy stays cold
        runs: list[Mapping[str, Any]] = [
            r
            async for r in rig.world.mongo.database()[Collection.STRATEGY_RUNS].find(
                {"strategy_name": "orb_v1", "session_date": DAY}
            )
        ]
        assert len(runs) == 1


class TestARequestThatCannotRun:
    async def test_an_unknown_strategy_fails_before_any_feed_is_opened(self, rig: Rig) -> None:
        feeds = ScriptedFeedOpener()

        with pytest.raises(ConfigurationError, match="no_such_strategy"):
            await rig.worker(feeds, start=("no_such_strategy",)).run()

        assert feeds.events == []

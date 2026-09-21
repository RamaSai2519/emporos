"""Browser acceptance harness: production HTTP API + production LIVE worker + real Atlas, over the
Angel One SmartAPI emulator (`FakeSmartApi`) — never a real broker, never a real order endpoint.

Mirrors `paper_server.py` exactly in shape (isolated account, command collection, kill-switch
collection, passcode collection; the same `ApiComposer`, which knows no difference between paper
and live). The only thing that changes is the worker underneath: `LiveWorkerComposer` over
`LiveVenue` and `AngelOneBroker`/`FakeSmartApi`, with `live_trading_enabled=True` so
`TradingModeGuard` is genuinely exercised open, not just proven to refuse. This is what lets the
dashboard be verified end to end against a LIVE-mode session — order placement, the risk gate, the
kill switch, square-off — without ever touching a real endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import replace
from datetime import date, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import uvicorn
from pymongo.asynchronous.collection import AsyncCollection
from tests.contract.harnesses import angelone
from tests.e2e.test_live_worker import FILL_PRICE, LiveTape, _cleanup, _FakeSocket
from tests.support.paper_rig import ID
from tests.support.worker_rig import EnterOnce, at, bar, strategy_config

from emporos.api.app import create_app
from emporos.api.auth import Argon2Passcodes, AuthService, LoginThrottle, TokenService
from emporos.cli.api_composition import ApiComposer
from emporos.cli.live_venue import LiveVenue
from emporos.cli.worker_composition import LiveWorkerComposer, WorkerTuning, bar_queue_for
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, FixedClock, SystemClock
from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.marketdata.session import SessionWindow
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.risk.kill_switch import FileSentinelKillSwitch
from emporos.session.risk_facts import VenueHealth
from emporos.session.worker import SessionSchedule
from emporos.strategies.registry import StrategyRegistry


class IsolatedPasscodes:
    """Stores this test's hash separately from the operator's real passcode."""

    def __init__(self, collection: AsyncCollection[Any]) -> None:
        self._collection = collection

    async def passcode_hash(self) -> str | None:
        record = await self._collection.find_one({"_id": "operator"})
        return str(record["hash"]) if record else None

    async def set_passcode_hash(self, hashed: str) -> None:
        await self._collection.replace_one({"_id": "operator"}, {"hash": hashed}, upsert=True)


class BrowserLiveTape(LiveTape):
    """Paces virtual market time so browser commands arrive during the live session."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(2)
        await super().sleep(seconds)


def _normal_browser_tape(clock: FixedClock, harness: Any, on_bar: Any) -> BrowserLiveTape:
    """A generous trading window: the browser needs real wall-clock minutes to click through the
    whole acceptance flow, and the worker's own session must still be TRADING throughout — the
    emulator/live composition costs more real time per poll cycle than the paper broker does."""
    tape = BrowserLiveTape(clock, harness, on_bar)
    minute = timedelta(minutes=1)
    when = at(9, 30) + minute
    while when <= at(11, 30):
        tape.ticks.append((when, FILL_PRICE, 400))
        when += minute
    tape.bars.append(bar(9, 30))  # closes 09:35, the strategy's entry
    return tape


class LiveBrowserServer:
    async def run(self) -> None:
        settings = Settings(ENV="local")
        if settings.db_name != "emporos_dev" or not settings.mongo_url:
            raise RuntimeError("Browser acceptance requires MONGO_URL for emporos_dev")
        mongo = MongoClientFactory(settings)
        db = mongo.database()
        suffix = IdGenerator().new_ulid().lower()
        account_id = f"DASHLIVE{suffix.upper()}"
        command_collection = f"zz_dashboard_live_commands_{suffix}"
        users_collection = f"zz_dashboard_live_users_{suffix}"
        kill_switch_collection = f"zz_dashboard_live_kill_{suffix}"
        worker: asyncio.Task[Any] | None = None
        with TemporaryDirectory(prefix="emporos-dashboard-live-") as directory:
            try:
                await MigrationRunner(MongoSchemaStore(db), PLATFORM_SCHEMA).apply()
                await db[command_collection].create_index("idempotency_key", unique=True)

                harness = angelone()
                health = VenueHealth()
                health.set_feed(True)  # socket-connection timing is test_live_venue.py's job
                venue = LiveVenue(harness.broker, _FakeSocket(), _FakeSocket(), [ID], health)

                registry = StrategyRegistry()
                registry.register(EnterOnce)
                # A wide reprice margin: real wall-clock pacing (needed so the session stays alive
                # for a human/browser) leaves much less headroom against poll_interval than the
                # deterministic proof has, and this session is about the composition, not a race
                # against the repricer (see tests/e2e/test_live_worker.py's identical override).
                base = strategy_config()
                config = base.model_copy(
                    update={
                        "execution": base.execution.model_copy(
                            update={"reprice_after_seconds": 3600}
                        )
                    }
                )
                bars = bar_queue_for([config])
                clock = FixedClock(at(9, 30))  # close to the entry bar: paced steps add real time
                tape = _normal_browser_tape(clock, harness, bars.on_candle)

                composer = LiveWorkerComposer(
                    client=mongo.client,
                    database=db,
                    broker=harness.broker,
                    venue=venue,
                    health=health,
                    live_trading_enabled=True,
                    bars=bars,
                    registry=registry,
                    configs=[config],
                    limits=RiskLimitsLoader().load(),
                    fees=FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21)),
                    account_id=account_id,
                    clock=clock,
                    sleeper=tape,
                    ids=IdGenerator(),
                    kill_switch_sentinel=FileSentinelKillSwitch(Path(directory) / "HALT"),
                    window=SessionWindow(),
                    tuning=WorkerTuning(
                        kill_switch=timedelta(seconds=30),
                        # PERIODIC reconciliation under this tape's real-time-paced auto-fill hits
                        # a genuine, separately-tracked race (EM-145): the adopter's "healed" fill
                        # never actually clears on re-comparison, halting every few virtual
                        # minutes. Long enough that this short interactive session never reaches
                        # it; EOD/STARTUP reconciliation (unaffected so far) still run.
                        reconcile=timedelta(hours=2),
                        schedule=SessionSchedule(
                            square_off_at=time(11, 0), close_at=time(11, 10),
                            poll_interval=timedelta(seconds=30),
                        ),  # fmt: skip
                    ),
                    session_date=date(2026, 9, 18),
                    kill_switch_collection=kill_switch_collection,
                    commands_collection=command_collection,
                )
                assembly = await composer.build()

                api_clock = SystemClock()
                services, lifespan = ApiComposer(
                    database=db, clock=api_clock, sleeper=AsyncioSleeper(), ids=IdGenerator(),
                    alerts=LogAlertSink(), account_id=account_id,
                    jwt_secret=secrets.token_bytes(32), limits=RiskLimitsLoader().load(),
                    commands_collection=command_collection,
                    kill_switch_collection=kill_switch_collection,
                    cors_origins=("http://127.0.0.1:3000", "http://localhost:3000"),
                ).build()  # fmt: skip
                auth = AuthService(
                    IsolatedPasscodes(db[users_collection]), Argon2Passcodes(),
                    TokenService(secrets.token_bytes(32), api_clock), LoginThrottle(api_clock),
                )  # fmt: skip
                await auth.set_passcode("dashboard-live-acceptance")
                services = replace(services, auth=auth)

                worker = asyncio.create_task(assembly.worker.run_session())
                server = uvicorn.Server(
                    uvicorn.Config(
                        create_app(services, lifespan), host="127.0.0.1", port=8012,
                        log_level="warning",
                    )  # fmt: skip
                )
                await server.serve()
            finally:
                if worker:
                    worker.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
                command_ids = [
                    item["_id"] async for item in db[command_collection].find({}, {"_id": 1})
                ]
                await db[Collection.COMMAND_RESULTS].delete_many(
                    {"command_id": {"$in": command_ids}}
                )
                await db.drop_collection(command_collection)
                await db.drop_collection(users_collection)
                await _cleanup(mongo, account_id, kill_switch_collection)  # drops kill switch too
                await mongo.close()


if __name__ == "__main__":
    asyncio.run(LiveBrowserServer().run())

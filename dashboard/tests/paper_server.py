"""Browser acceptance harness: production HTTP API + production paper worker + real Atlas.

Uses a unique paper account, command collection and passcode collection. Run serially after the
repository integration suite because its existing worker fixture owns shared test-strategy rows.
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import uvicorn
from pymongo.asynchronous.collection import AsyncCollection

from emporos.api.app import create_app
from emporos.api.auth import Argon2Passcodes, AuthService, LoginThrottle, TokenService
from emporos.cli.api_composition import ApiComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.risk.config import RiskLimitsLoader
from tests.support.worker_rig import Tape, WorkerWorld, cleanup, normal_tape


class IsolatedPasscodes:
    """Stores this test's hash separately from the operator's real passcode."""

    def __init__(self, collection: AsyncCollection[Any]) -> None:
        self._collection = collection

    async def passcode_hash(self) -> str | None:
        record = await self._collection.find_one({"_id": "operator"})
        return str(record["hash"]) if record else None

    async def set_passcode_hash(self, hashed: str) -> None:
        await self._collection.replace_one({"_id": "operator"}, {"hash": hashed}, upsert=True)


class BrowserTape(Tape):
    """Paces virtual market time so browser commands arrive during the paper session."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(2)
        await super().sleep(seconds)


class PaperBrowserServer:
    async def run(self) -> None:
        settings = Settings(ENV="local")
        if settings.db_name != "emporos_dev" or not settings.mongo_url:
            raise RuntimeError("Browser acceptance requires MONGO_URL for emporos_dev")
        mongo = MongoClientFactory(settings)
        db = mongo.database()
        suffix = IdGenerator().new_ulid().lower()
        command_collection = f"zz_dashboard_commands_{suffix}"
        users_collection = f"zz_dashboard_users_{suffix}"
        worker: asyncio.Task[Any] | None = None
        with TemporaryDirectory(prefix="emporos-dashboard-") as directory:
            world = WorkerWorld(mongo=mongo, client=mongo.client, account_id=f"DASH{suffix.upper()}", sentinel_path=Path(directory) / "HALT", kill_switch_collection=f"zz_dashboard_kill_{suffix}")
            try:
                await MigrationRunner(MongoSchemaStore(db), PLATFORM_SCHEMA).apply()
                await db[command_collection].create_index("idempotency_key", unique=True)
                source = normal_tape(world.clock, world.market, self.ignore_bar)
                tape = BrowserTape(clock=source.clock, market=source.market, on_bar=source.on_bar, ticks=source.ticks, bars=source.bars)
                assembly, _ = await world.build(tape=tape, commands_collection=command_collection)
                clock = SystemClock()
                services, lifespan = ApiComposer(database=db, clock=clock, sleeper=AsyncioSleeper(), ids=IdGenerator(), alerts=LogAlertSink(), account_id=world.account_id, jwt_secret=secrets.token_bytes(32), limits=RiskLimitsLoader().load(), commands_collection=command_collection, kill_switch_collection=world.kill_switch_collection, cors_origins=("http://127.0.0.1:3000", "http://localhost:3000")).build()
                auth = AuthService(IsolatedPasscodes(db[users_collection]), Argon2Passcodes(), TokenService(secrets.token_bytes(32), clock), LoginThrottle(clock))
                await auth.set_passcode("dashboard-paper-acceptance")
                services = replace(services, auth=auth)
                worker = asyncio.create_task(assembly.worker.run_session())
                server = uvicorn.Server(uvicorn.Config(create_app(services, lifespan), host="127.0.0.1", port=8011, log_level="warning"))
                await server.serve()
            finally:
                if worker:
                    worker.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
                command_ids = [item["_id"] async for item in db[command_collection].find({}, {"_id": 1})]
                await db[Collection.COMMAND_RESULTS].delete_many({"command_id": {"$in": command_ids}})
                await db.drop_collection(command_collection)
                await db.drop_collection(users_collection)
                await cleanup(world)
                await mongo.close()

    def ignore_bar(self, _bar: object) -> None:
        """Replaced by WorkerWorld.build with the worker's public bar queue callback."""


if __name__ == "__main__":
    asyncio.run(PaperBrowserServer().run())

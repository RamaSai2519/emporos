"""EM-87 — every command in plan.md §15.1 works end to end: HTTP, `commands`, the worker, risk and
execution → the books, with the status transitions visible through the API at every step.

A real worker session (production composer, virtual time, real Atlas) runs while the operator's
commands arrive over the real API app. Nothing is optimistic: each assertion reads the command back
through `GET /commands/{id}` after the worker has processed it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest

from emporos.api.app import create_app
from emporos.cli.api_composition import ApiComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper
from emporos.core.ids import IdGenerator
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.risk.config import RiskLimitsLoader
from emporos.session.lifecycle import SessionState
from tests.support.worker_rig import WorkerWorld, at, cleanup

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(dev_settings: Any, tmp_path: Any) -> AsyncIterator[WorkerWorld]:
    mongo = MongoClientFactory(dev_settings)
    suffix = IdGenerator().new_ulid().lower()
    w = WorkerWorld(
        mongo=mongo, client=mongo.client, account_id=f"CTLIT{suffix.upper()}",
        sentinel_path=tmp_path / "HALT", kill_switch_collection=f"zz_kill_switch_{suffix}",
    )  # fmt: skip
    await MigrationRunner(MongoSchemaStore(mongo.database()), PLATFORM_SCHEMA).apply()
    backup = await mongo.database()[Collection.USERS].find_one({"_id": "operator"})
    try:
        yield w
    finally:
        db = mongo.database()
        await db[Collection.COMMANDS].delete_many(
            {"idempotency_key": {"$regex": f"^ctlit-{suffix}"}}
        )
        await db[Collection.COMMAND_RESULTS].delete_many(
            {"created_at": {"$gte": at(0, 0), "$lt": at(23, 59)}}
        )
        await db[Collection.USERS].delete_many({"_id": "operator"})
        if backup is not None:
            await db[Collection.USERS].insert_one(backup)
        await cleanup(w)
        await mongo.close()


class Operator:
    """What the dashboard would do: log in, send commands, read them back."""

    def __init__(
        self, world: WorkerWorld, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> None:
        self.world, self.client, self.headers = world, client, headers
        self.sent: dict[str, str] = {}
        self._n = 0

    async def send(self, label: str, kind: str, **params: Any) -> None:
        self._n += 1
        key = f"ctlit-{self.world.account_id.removeprefix('CTLIT').lower()}-{self._n}-{label}"
        response = await self.client.post(
            "/commands", headers=self.headers,
            json={"idempotency_key": key, "type": kind, "params": params},
        )  # fmt: skip
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "PENDING"  # never optimistic: it has not run yet
        self.sent[label] = response.json()["id"]

    async def detail(self, label: str) -> dict[str, Any]:
        response = await self.client.get(f"/commands/{self.sent[label]}", headers=self.headers)
        return response.json()  # type: ignore[no-any-return]

    async def status(self, label: str) -> str:
        return (await self.detail(label))["command"]["status"]  # type: ignore[no-any-return]

    async def trail(self, label: str) -> list[str]:
        return [r["status"] for r in (await self.detail(label))["results"] if r["status"]]


def scheduled(
    op: Operator, sends: list[tuple[str, str, dict[str, Any]]]
) -> Callable[[], Awaitable[object]]:
    async def run() -> None:
        for label, kind, params in sends:
            await op.send(label, kind, **params)

    return run


def cancel_resting(op: Operator) -> Callable[[], Awaitable[object]]:
    """The resting order's id is known only once the worker has placed it: read it back."""

    async def run() -> None:
        placed = await op.detail("resting")
        done = [r for r in placed["results"] if r["status"] == "DONE"]
        assert done, placed  # the resting order must have been placed by now
        order_id = done[0]["data"]["order_id"]
        await op.send("cancel", "CANCEL_ORDER", order_id=order_id)

    return run


async def test_every_command_runs_through_the_worker_with_visible_status_transitions(
    world: WorkerWorld,
) -> None:
    async def fake_backtest(params: dict[str, Any]) -> dict[str, Any]:
        return {"trades": 0, "strategy": params["strategy"]}

    assembly, tape = await world.build(job_runners={"backtest": fake_backtest})
    services, lifespan = ApiComposer(
        database=world.mongo.database(), clock=world.clock, sleeper=AsyncioSleeper(),
        ids=IdGenerator(), alerts=LogAlertSink(), account_id=world.account_id,
        jwt_secret=b"k" * 32, limits=RiskLimitsLoader().load(),
        kill_switch_collection=world.kill_switch_collection,
    ).build()  # fmt: skip
    app = create_app(services, lifespan)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as client:
        await services.auth.set_passcode("correct horse battery")
        token = (
            await client.post("/auth/login", json={"passcode": "correct horse battery"})
        ).json()
        op = Operator(world, client, {"Authorization": f"Bearer {token['token']}"})

        config_update = {"name": "enter_once_test", "config": {"x": "1"}}
        backtest = {"strategy": "enter_once_test", "start": "2026-01-01", "end": "2026-02-01"}
        manual = {
            "instrument_id": "NSE:3045",
            "side": "BUY",
            "quantity": 50,
            "limit_price": "100.00",
            "reason": "top up",
        }
        rest = {**manual, "quantity": 10, "limit_price": "99.00", "reason": "rest"}
        fat = {**manual, "quantity": 1000, "reason": "fat finger"}
        halted = {**manual, "reason": "while halted"}
        who = op
        sbin = {"instrument_id": "NSE:3045"}
        halt = {"halted": True, "reason": "test halt"}
        tape.hooks += [
            (at(9, 37), scheduled(who, [
                ("reconcile", "RECONCILE_NOW", {}),
                ("mode", "SET_TRADING_MODE", {"mode": "LIVE"}),
                ("unknown-strategy", "START_STRATEGY", {"name": "no_such_strategy"}),
                ("config-midsession", "UPDATE_STRATEGY_CONFIG", config_update),
                ("backtest", "RUN_BACKTEST", backtest),
                ("backfill", "TRIGGER_BACKFILL", {"instrument_ids": ["NSE:3045"], "days": 3}),
            ])),
            (at(9, 39), scheduled(who, [("manual-ok", "PLACE_MANUAL_ORDER", manual)])),
            (at(9, 40), scheduled(who, [("resting", "PLACE_MANUAL_ORDER", rest)])),
            (at(9, 41), scheduled(who, [("stop", "STOP_STRATEGY", {"name": "enter_once_test"})])),
            (at(9, 42), cancel_resting(who)),
            (at(9, 43), scheduled(who, [("close", "CLOSE_POSITION", sbin)])),
            (at(9, 44), scheduled(who, [("manual-too-big", "PLACE_MANUAL_ORDER", fat)])),
            (at(9, 45), scheduled(who, [("halt", "SET_KILL_SWITCH", halt)])),
            (at(9, 46), scheduled(who, [("blocked", "PLACE_MANUAL_ORDER", halted)])),
            (at(9, 47), scheduled(who, [("resume", "SET_KILL_SWITCH", {"halted": False})])),
            (at(9, 49), scheduled(who, [("square-all", "SQUARE_OFF_ALL", {"reason": "operator"})])),
        ]  # fmt: skip
        report = await assembly.worker.run_session()
        assert report.final_state is SessionState.SHUTTING_DOWN and report.failure == ""

        # Nothing was executed by the API: every command's outcome was produced by the worker.
        assert await op.status("reconcile") == "DONE"
        assert (await op.detail("reconcile"))["command"]["issued_by"] == "operator"
        assert await op.trail("reconcile") == ["ACCEPTED", "EXECUTING", "DONE"]

        assert await op.status("mode") == "REJECTED"
        assert "deployment" in (await op.detail("mode"))["command"]["reason"]
        assert await op.status("unknown-strategy") == "FAILED"
        assert await op.status("config-midsession") == "REJECTED"
        assert await op.status("backtest") == "DONE"
        assert any(r["message"] == "job finished" for r in (await op.detail("backtest"))["results"])
        assert await op.status("backfill") == "REJECTED"  # no backfill runner in a paper worker

        assert await op.status("manual-ok") == "DONE"
        assert await op.status("manual-too-big") == "REJECTED"
        reason = (await op.detail("manual-too-big"))["command"]["reason"]
        assert reason.split(":")[0] in ("MaxPositionValueGuard", "MaxOrderQuantityGuard"), reason
        assert await op.status("resting") == "DONE"
        assert await op.status("cancel") == "DONE"
        assert (await op.detail("cancel"))["results"][-1]["data"] == {"state": "CANCELLED"}
        assert await op.status("stop") == "DONE"
        assert await op.status("close") == "DONE"
        assert await op.status("halt") == "DONE"
        assert (
            await op.status("blocked") == "REJECTED"
        )  # the kill switch refuses a manual order too
        assert "KillSwitchGuard" in (await op.detail("blocked"))["command"]["reason"]
        assert await op.status("resume") == "DONE"
        assert await op.status("square-all") == "DONE"

        events = (
            await client.get("/system/events", headers=op.headers, params={"limit": 200})
        ).json()
        states = [e["data"]["to"] for e in events if e["type"] == "session_state"]
        assert (
            "HALTED" in states and states.count("TRADING") >= 2
        )  # halted by command, resumed by command
        orders = (await client.get("/orders", headers=op.headers, params={"limit": 50})).json()
        assert all(o["state"] in ("FILLED", "CANCELLED") for o in orders), [
            o["state"] for o in orders
        ]
        assert report.flat_at_close is True

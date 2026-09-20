"""EM-88/89/90/91 — the control-plane API against real Atlas, called over real HTTP semantics.

Composed by the production composer. The worker is NOT running: commands stay PENDING, which is
exactly what lets the tests show the API records them once and never executes anything itself.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from emporos.api.app import ApiServices, _events, create_app
from emporos.api.auth import Argon2Passcodes
from emporos.cli.api_composition import ApiComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import (
    ExecutionRecord,
    KillSwitchRecord,
    OrderEventRecord,
    OrderRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    ReconciliationRunRecord,
    RiskEventRecord,
    StrategyRecord,
    StrategyRunRecord,
    SystemEventRecord,
)
from emporos.persistence.repositories import (
    ExecutionRepository,
    KillSwitchRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
    ReconciliationRunRepository,
    RiskEventRepository,
    StrategyRepository,
    StrategyRunRepository,
    SystemEventRepository,
)
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.risk.config import RiskLimitsLoader

pytestmark = pytest.mark.integration
PASSCODE = "correct horse battery"


class World:
    def __init__(self, mongo: MongoClientFactory) -> None:
        self.mongo = mongo
        self.db = mongo.database()
        self.suffix = IdGenerator().new_ulid().lower()
        self.account = f"APIIT{self.suffix.upper()}"
        self.switch = f"zz_kill_switch_{self.suffix}"
        self.clock = SystemClock()
        self.command_keys: list[str] = []
        self.user_backup: dict[str, Any] | None = None

    def composer(self, **kw: Any) -> ApiComposer:
        return ApiComposer(
            database=self.db, clock=self.clock, sleeper=AsyncioSleeper(), ids=IdGenerator(),
            alerts=LogAlertSink(), account_id=self.account, jwt_secret=b"k" * 32,
            limits=RiskLimitsLoader().load(), kill_switch_collection=self.switch, **kw,
        )  # fmt: skip


@pytest.fixture
async def world(dev_settings: Any) -> AsyncIterator[World]:
    mongo = MongoClientFactory(dev_settings)
    await MigrationRunner(MongoSchemaStore(mongo.database()), PLATFORM_SCHEMA).apply()
    w = World(mongo)
    # The passcode lives in ONE document, `users/operator`: keep whatever is there and restore it.
    w.user_backup = await w.db[Collection.USERS].find_one({"_id": "operator"})
    try:
        yield w
    finally:
        mine = {"account_id": w.account}
        ids = [d["_id"] async for d in w.db[Collection.ORDERS].find(mine, {"_id": 1})]
        for name in (Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS,
                     Collection.PORTFOLIO_SNAPSHOTS):  # fmt: skip
            await w.db[name].delete_many(mine)
        await w.db[Collection.ORDER_EVENTS].delete_many({"order_id": {"$in": ids}})
        await w.db[Collection.COMMANDS].delete_many({"idempotency_key": {"$in": w.command_keys}})
        for collection, query in (
            (Collection.STRATEGIES, {"name": f"apiit_{w.suffix}"}),
            (Collection.STRATEGY_RUNS, {"strategy_id": f"strat-{w.suffix}"}),
            (Collection.RISK_EVENTS, {"_id": f"risk-{w.suffix}"}),
            (Collection.SYSTEM_EVENTS, {"_id": f"evt-{w.suffix}"}),
            (Collection.RECONCILIATION_RUNS, {"_id": f"rec-{w.suffix}"}),
        ):  # fmt: skip
            await w.db[collection].delete_many(query)
        await w.db.drop_collection(w.switch)
        await w.db[Collection.USERS].delete_many({"_id": "operator"})
        if w.user_backup is not None:
            await w.db[Collection.USERS].insert_one(w.user_backup)
        await mongo.close()


async def client_for(world: World, **kw: Any) -> tuple[httpx.AsyncClient, ApiServices]:
    services, lifespan = world.composer(**kw).build()
    app = create_app(services, lifespan)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://api"), services


async def login(client: httpx.AsyncClient, world: World, services: ApiServices) -> dict[str, str]:
    await services.auth.set_passcode(PASSCODE)
    response = await client.post("/auth/login", json={"passcode": PASSCODE})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


class TestAuthentication:
    async def test_login_issues_a_token_that_opens_the_protected_routes(self, world: World) -> None:
        client, services = await client_for(world)
        async with client:
            headers = await login(client, world, services)
            assert (await client.get("/health")).json()["status"] == "ok"
            assert (await client.get("/overview", headers=headers)).status_code == 200
            stored = await world.db[Collection.USERS].find_one({"_id": "operator"})
            assert stored and stored["passcode_hash"].startswith("$argon2id$")
            assert PASSCODE not in str(stored)  # the passcode is never stored

    async def test_a_wrong_passcode_is_401_and_says_nothing_else(self, world: World) -> None:
        client, services = await client_for(world)
        async with client:
            await services.auth.set_passcode(PASSCODE)
            response = await client.post("/auth/login", json={"passcode": "wrong wrong"})
            assert response.status_code == 401 and response.json() == {"detail": "invalid passcode"}

    async def test_the_login_route_is_rate_limited(self, world: World) -> None:
        client, services = await client_for(world)
        async with client:
            await services.auth.set_passcode(PASSCODE)
            codes = [
                (await client.post("/auth/login", json={"passcode": "wrong wrong"})).status_code
                for _ in range(7)
            ]
            assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
            limited = await client.post("/auth/login", json={"passcode": PASSCODE})
            assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/overview", "get"), ("/positions", "get"), ("/orders", "get"), ("/orders/x", "get"),
            ("/executions", "get"), ("/strategies", "get"), ("/risk", "get"),
            ("/system/events", "get"), ("/system/reconciliations", "get"), ("/commands", "get"),
            ("/commands/x", "get"), ("/commands", "post"), ("/stream", "get"),
        ],
    )  # fmt: skip
    async def test_every_protected_route_refuses_a_missing_tampered_or_expired_token(
        self, world: World, path: str, method: str
    ) -> None:
        client, services = await client_for(world)
        async with client:
            good = (await login(client, world, services))["Authorization"]
            head, body, sig = good[7:].split(".")
            expired = (
                services.auth._tokens.__class__(b"k" * 32, world.clock, timedelta(seconds=-5))
                .issue("operator")
                .value
            )
            bearers = ["", "Bearer ", f"Bearer {head}.{body}.{sig[:-3]}abc", f"Bearer {expired}"]
            for bearer in bearers:
                headers = {"Authorization": bearer} if bearer else {}
                response = await client.request(
                    method, path, headers=headers, json={} if method == "post" else None
                )
                assert response.status_code == 401, (path, bearer)


class TestReads:
    async def _seed(self, world: World) -> dict[str, str]:
        now = world.clock.now()
        order = OrderRecord(
            _id=f"ord-{world.suffix}", idempotency_key=f"idem-{world.suffix}",
            ordertag=f"tag{world.suffix}"[:20], instrument_id="NSE:3045", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=10, limit_price=Money.of("100.05"),
            state="FILLED", session_date="2026-09-18", filled_quantity=10,
            average_price=Money.of("100.05"), created_at=now, updated_at=now,
            account_id=world.account, broker_order_id="b1",
        )  # fmt: skip
        await OrderRepository(world.db).insert(order)
        events = OrderEventRepository(world.db)
        await events.insert(
            OrderEventRecord(
                _id=f"{order.id}:1", order_id=order.id, seq=1, ts=now, state="PENDING_NEW"
            )
        )
        await events.insert(
            OrderEventRecord(
                _id=f"{order.id}:2",
                order_id=order.id,
                seq=2,
                ts=now,
                state="FILLED",
                filled_quantity=10,
            )
        )
        await ExecutionRepository(world.db).insert(
            ExecutionRecord(
                _id=f"exec-{world.suffix}", broker_trade_id=f"t-{world.suffix}", order_id=order.id,
                instrument_id="NSE:3045", side=OrderSide.BUY, quantity=10, price=Money.of("100.05"),
                ts=now, account_id=world.account, fees=Money.of("2.5"),
            )
        )  # fmt: skip
        await PositionRepository(world.db).insert(
            PositionRecord(
                _id=f"pos-{world.suffix}", account_id=world.account, instrument_id="NSE:3045",
                net_quantity=10, average_price=Money.of("100.05"), realised_pnl=Money.of("-2.5"),
                updated_at=now, fees=Money.of("2.5"),
            )
        )  # fmt: skip
        await PortfolioSnapshotRepository(world.db).insert(
            PortfolioSnapshotRecord(
                _id=f"snap-{world.suffix}", account_id=world.account, ts=now, kind="INTRADAY",
                realised_pnl=Money.of("-2.5"), unrealised_pnl=Money.of("4.5"),
                fees=Money.of("2.5"), trades=1,
            )
        )  # fmt: skip
        await KillSwitchRepository(world.db, world.switch).save(
            KillSwitchRecord(
                _id="kill_switch", halted=True, reason="testing", set_by="rama", changed_at=now
            )
        )
        await StrategyRepository(world.db).insert(
            StrategyRecord(_id=f"strat-{world.suffix}", name=f"apiit_{world.suffix}")
        )
        await StrategyRunRepository(world.db).insert(
            StrategyRunRecord(
                _id=f"run-{world.suffix}",
                strategy_id=f"strat-{world.suffix}",
                session_date="2026-09-18",
                created_at=now,
                config_hash="abc123",
            )
        )
        await RiskEventRepository(world.db).insert(
            RiskEventRecord(
                _id=f"risk-{world.suffix}",
                rule="MaxOrderQuantityGuard",
                ts=now,
                reason="too big",
                instrument_id="NSE:3045",
            )
        )
        await SystemEventRepository(world.db).insert(
            SystemEventRecord.model_validate(
                {
                    "_id": f"evt-{world.suffix}",
                    "type": "session_state",
                    "ts": now + timedelta(days=3650),
                    "to": "TRADING",
                }
            )
        )
        await ReconciliationRunRepository(world.db).insert(
            ReconciliationRunRecord(
                _id=f"rec-{world.suffix}",
                status="CLEAN",
                ts=now + timedelta(days=3650),
                trigger="STARTUP",
            )
        )
        return {"order": order.id}

    async def test_every_read_endpoint_returns_what_was_persisted_with_exact_money(
        self, world: World
    ) -> None:
        ids = await self._seed(world)
        client, services = await client_for(world)
        async with client:
            headers = await login(client, world, services)
            overview = (await client.get("/overview", headers=headers)).json()
            assert overview["session_state"] == "TRADING" and overview["open_positions"] == 1
            assert (
                overview["kill_switch"]["halted"] is True
                and overview["kill_switch"]["set_by"] == "rama"
            )
            assert (overview["realised_pnl"], overview["unrealised_pnl"], overview["trades"]) == (
                "-2.5",
                "4.5",
                1,
            )
            assert overview["reconciliation"]["status"] == "CLEAN"

            (position,) = (await client.get("/positions", headers=headers)).json()
            assert (
                position["net_quantity"],
                position["average_price"],
                position["realised_pnl"],
            ) == (10, "100.05", "-2.5")
            (order,) = (
                await client.get("/orders", headers=headers, params={"state": "FILLED"})
            ).json()
            assert order["id"] == ids["order"] and order["limit_price"] == "100.05"
            assert (
                await client.get("/orders", headers=headers, params={"state": "OPEN"})
            ).json() == []

            detail = (await client.get(f"/orders/{ids['order']}", headers=headers)).json()
            assert [e["seq"] for e in detail["events"]] == [1, 2] and detail["events"][-1][
                "state"
            ] == "FILLED"
            assert (await client.get("/orders/nope", headers=headers)).status_code == 404

            (execution,) = (await client.get("/executions", headers=headers)).json()
            assert (execution["price"], execution["fees"]) == ("100.05", "2.5")
            strategy = next(
                s
                for s in (await client.get("/strategies", headers=headers)).json()
                if s["name"] == f"apiit_{world.suffix}"
            )
            assert strategy["config_hash"] == "abc123" and strategy["last_run_date"] == "2026-09-18"
            risk = (await client.get("/risk", headers=headers)).json()
            assert risk["limits"]["max_order_quantity"] == "500"
            assert any(r["rule"] == "MaxOrderQuantityGuard" for r in risk["recent_rejections"])
            events = (await client.get("/system/events", headers=headers)).json()
            assert any(
                e["id"] == f"evt-{world.suffix}" and e["data"]["to"] == "TRADING" for e in events
            )
            runs = (await client.get("/system/reconciliations", headers=headers)).json()
            assert any(r["id"] == f"rec-{world.suffix}" for r in runs)

    async def test_another_accounts_orders_are_invisible(self, world: World) -> None:
        ids = await self._seed(world)
        stranger = World(world.mongo)
        client, services = await client_for(stranger)
        async with client:
            headers = await login(client, stranger, services)
            assert (await client.get("/orders", headers=headers)).json() == []
            assert (await client.get(f"/orders/{ids['order']}", headers=headers)).status_code == 404
            assert (await client.get("/positions", headers=headers)).json() == []
        await world.db.drop_collection(stranger.switch)


class TestCommands:
    def key(self, world: World, label: str) -> str:
        key = f"apiit-{world.suffix}-{label}"
        world.command_keys.append(key)
        return key

    async def test_a_command_is_accepted_recorded_pending_and_visible_at_once(
        self, world: World
    ) -> None:
        client, services = await client_for(world)
        async with client:
            headers = await login(client, world, services)
            body = {
                "idempotency_key": self.key(world, "a"),
                "type": "CANCEL_ORDER",
                "params": {"order_id": "o1"},
            }
            response = await client.post("/commands", headers=headers, json=body)
            assert response.status_code == 202
            command = response.json()
            assert (command["status"], command["issued_by"], command["attempts"]) == (
                "PENDING",
                "operator",
                0,
            )
            detail = (await client.get(f"/commands/{command['id']}", headers=headers)).json()
            assert detail["command"]["status"] == "PENDING" and detail["results"] == []
            listed = (await client.get("/commands", headers=headers)).json()
            assert any(c["id"] == command["id"] for c in listed)
            assert (await client.get("/commands/nope", headers=headers)).status_code == 404

    async def test_a_double_submit_creates_one_command_a_conflicting_reuse_is_409_a_bad_body_422(
        self, world: World
    ) -> None:
        client, services = await client_for(world)
        async with client:
            headers = await login(client, world, services)
            body = {
                "idempotency_key": self.key(world, "b"),
                "type": "CANCEL_ORDER",
                "params": {"order_id": "o1"},
            }
            first = await client.post("/commands", headers=headers, json=body)
            second = await client.post("/commands", headers=headers, json=body)  # a double click
            assert first.json()["id"] == second.json()["id"]
            in_db = await world.db[Collection.COMMANDS].count_documents(
                {"idempotency_key": body["idempotency_key"]}
            )
            assert in_db == 1
            other = {**body, "params": {"order_id": "o2"}}
            assert (await client.post("/commands", headers=headers, json=other)).status_code == 409
            bad = {"idempotency_key": self.key(world, "c"), "type": "CANCEL_ORDER", "params": {}}
            assert (await client.post("/commands", headers=headers, json=bad)).status_code == 422
            unknown = {"idempotency_key": self.key(world, "d"), "type": "DROP_TABLES", "params": {}}
            assert (
                await client.post("/commands", headers=headers, json=unknown)
            ).status_code == 422
            market = {"idempotency_key": self.key(world, "e"), "type": "PLACE_MANUAL_ORDER",
                      "params": {"instrument_id": "NSE:1", "side": "BUY", "quantity": 1,
                                 "limit_price": 100.5, "reason": "x"}}  # fmt: skip
            assert (
                await client.post("/commands", headers=headers, json=market)
            ).status_code == 422  # a float price
            assert (
                await world.db[Collection.COMMANDS].count_documents(
                    {"idempotency_key": {"$in": world.command_keys}}
                )
                == 1
            )

    async def test_set_trading_mode_is_recorded_so_the_worker_can_reject_it_visibly(
        self, world: World
    ) -> None:
        client, services = await client_for(world)
        async with client:
            headers = await login(client, world, services)
            body = {
                "idempotency_key": self.key(world, "f"),
                "type": "SET_TRADING_MODE",
                "params": {"mode": "LIVE"},
            }
            assert (await client.post("/commands", headers=headers, json=body)).status_code == 202


class TestOpenApi:
    async def test_the_schema_describes_every_route_with_typed_responses(
        self, world: World
    ) -> None:
        client, _ = await client_for(world)
        async with client:
            schema = (await client.get("/openapi.json")).json()
        assert schema["openapi"].startswith("3.")
        paths = schema["paths"]
        for path in (
            "/overview",
            "/positions",
            "/orders",
            "/orders/{order_id}",
            "/commands",
            "/stream",
            "/auth/login",
        ):
            assert path in paths
        for path, operations in paths.items():
            for method, operation in operations.items():
                assert operation.get("operationId"), (path, method)
        assert "OrderDto" in schema["components"]["schemas"]
        assert (
            schema["components"]["schemas"]["OrderDto"]["properties"]["limit_price"]["type"]
            == "string"
        )


class TestStream:
    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    async def test_a_change_reaches_the_stream_within_two_seconds(self, world: World) -> None:
        _, services = await client_for(world)
        services = ApiServicesFast(services)
        generator = _events(self._Request(), services, None)  # type: ignore[arg-type]
        assert await anext(generator) == "retry: 2000\n\n"
        started = time.monotonic()
        now = world.clock.now()

        async def write() -> None:
            await asyncio.sleep(0.3)
            await OrderRepository(world.db).insert(
                OrderRecord(
                    _id=f"sse-{world.suffix}", idempotency_key=f"sse-idem-{world.suffix}",
                    ordertag=f"sse{world.suffix}"[:20], instrument_id="NSE:3045",
                    side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=1,
                    limit_price=Money.of("1"), state="OPEN",
                    session_date="2026-09-18", created_at=now, updated_at=world.clock.now(),
                    account_id=world.account,
                )
            )  # fmt: skip

        writer = asyncio.create_task(write())
        chunk = await asyncio.wait_for(anext(generator), timeout=5)
        await writer
        elapsed = time.monotonic() - started
        assert "event: order" in chunk and f"sse-{world.suffix}" in chunk and elapsed < 2.0, (
            chunk,
            elapsed,
        )

    async def test_a_reconnect_resumes_from_last_event_id_with_nothing_missed_or_repeated(
        self, world: World
    ) -> None:
        _, services = await client_for(world)
        services = ApiServicesFast(services)
        orders = OrderRepository(world.db)
        base = world.clock.now()

        def order(n: int, at: Any) -> OrderRecord:
            return OrderRecord(
                _id=f"sse{n}-{world.suffix}", idempotency_key=f"sse{n}-{world.suffix}",
                ordertag=f"s{n}{world.suffix}"[:20], instrument_id="NSE:3045", side=OrderSide.BUY,
                order_type=OrderType.LIMIT, quantity=1, limit_price=Money.of("1"), state="OPEN",
                session_date="2026-09-18", created_at=base, updated_at=at, account_id=world.account,
            )  # fmt: skip

        since = f"{(base - timedelta(seconds=1)).isoformat()}|order|"
        for n in (1, 2, 3):
            await orders.insert(order(n, base + timedelta(milliseconds=10 * n)))
        first = await _drain(_events(self._Request(), services, since), 3)
        assert [
            f"sse{n}-{world.suffix}" in e for n, e in zip((1, 2, 3), first[1:], strict=True)
        ] == [True] * 3
        last_id = first[-1].split("\n", 1)[0].removeprefix("id: ")
        await orders.insert(order(4, base + timedelta(milliseconds=100)))  # while "disconnected"
        again = await _drain(_events(self._Request(), services, last_id), 2)
        joined = "".join(again)
        assert (
            f"sse4-{world.suffix}" in joined
            and f"sse3-{world.suffix}" not in joined
            and f"sse1-{world.suffix}" not in joined
        )


def ApiServicesFast(services: ApiServices) -> ApiServices:
    from dataclasses import replace

    return replace(services, stream_poll_seconds=0.1, stream_limit=6)


async def _drain(generator: AsyncIterator[str], polls: int) -> list[str]:
    return [chunk async for chunk in generator]


class TestTheApiCannotReachAngelOne:
    def test_importing_and_building_the_api_never_loads_any_broker_code(self) -> None:
        """A fresh interpreter imports the API app and its composition root. If any broker module
        (or the SmartAPI SDK) got loaded along the way, this fails."""
        program = (
            "import sys\n"
            "import emporos.api.app, emporos.api.queries, emporos.api.auth\n"
            "import emporos.control.submitter\n"
            "import emporos.cli.api_composition\n"
            "bad = sorted(m for m in sys.modules if m == 'SmartApi' or m.startswith('SmartApi.') "
            "or m == 'emporos.broker' or m.startswith('emporos.broker.'))\n"
            "print('LOADED:' + ','.join(bad))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "LOADED:", out.stdout


def test_argon2_is_used_for_the_passcode() -> None:
    assert Argon2Passcodes().hash("a long enough passcode").startswith("$argon2id$")

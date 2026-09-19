"""Every repository against real Atlas `emporos_dev` (EM-21): typed round-trips, exact money,
and typed errors on unique-index collisions. Records are cleaned up after each test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.money import Money
from emporos.persistence import repositories as repos
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.records import Record
from emporos.persistence.repository import Repository
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.records import RecordFactory

pytestmark = pytest.mark.integration

Database = AsyncDatabase[Mapping[str, Any]]

_REPOSITORIES: list[tuple[type[Repository[Any]], Callable[[RecordFactory], Record]]] = [
    (repos.UserRepository, RecordFactory.user),
    (repos.AccountRepository, RecordFactory.account),
    (repos.InstrumentRepository, RecordFactory.instrument),
    (repos.InstrumentVersionRepository, RecordFactory.instrument_version),
    (repos.StrategyRepository, RecordFactory.strategy),
    (repos.StrategyRunRepository, RecordFactory.strategy_run),
    (repos.SignalRepository, RecordFactory.signal),
    (repos.OrderRepository, RecordFactory.order),
    (repos.OrderEventRepository, RecordFactory.order_event),
    (repos.ExecutionRepository, RecordFactory.execution),
    (repos.PositionRepository, RecordFactory.position),
    (repos.PortfolioSnapshotRepository, RecordFactory.portfolio_snapshot),
    (repos.RiskEventRepository, RecordFactory.risk_event),
    (repos.ReconciliationRunRepository, RecordFactory.reconciliation_run),
    (repos.BacktestRunRepository, RecordFactory.backtest_run),
    (repos.BacktestTradeRepository, RecordFactory.backtest_trade),
    (repos.SystemEventRepository, RecordFactory.system_event),
    (repos.MarketCalendarRepository, RecordFactory.market_calendar),
    (repos.CommandRepository, RecordFactory.command),
    (repos.CommandResultRepository, RecordFactory.command_result),
]


@pytest.fixture
async def migrated(database: Database) -> Database:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    return database


@pytest.fixture
async def cleanup(migrated: Database) -> AsyncIterator[Callable[[Repository[Any], Record], None]]:
    tracked: list[tuple[Repository[Any], str]] = []

    def track(repo: Repository[Any], record: Record) -> None:
        tracked.append((repo, record.id))

    yield track
    for repo, record_id in tracked:
        await repo.delete(record_id)


@pytest.mark.parametrize(
    ("repo_type", "make"), _REPOSITORIES, ids=[repo.__name__ for repo, _ in _REPOSITORIES]
)
async def test_repository_insert_get_replace_find_count_delete(
    migrated: Database,
    cleanup: Callable[[Repository[Any], Record], None],
    repo_type: Callable[[Database], Repository[Any]],
    make: Callable[[RecordFactory], Record],
) -> None:
    repo = repo_type(migrated)
    record = make(RecordFactory())
    cleanup(repo, record)

    await repo.insert(record)

    assert await repo.get(record.id) == record
    assert await repo.count({"_id": record.id}) == 1
    assert await repo.find({"_id": record.id}) == [record]
    await repo.replace(record)
    assert await repo.delete(record.id) is True
    assert await repo.get(record.id) is None
    assert await repo.delete(record.id) is False


async def test_order_money_is_stored_as_decimal128_and_read_back_exactly(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    orders = repos.OrderRepository(migrated)
    order = RecordFactory().order(limit_price=Money.of("101.35"))
    cleanup(orders, order)
    await orders.insert(order)

    raw = await migrated["orders"].find_one({"_id": order.id})
    restored = await orders.get(order.id)

    assert raw is not None
    assert type(raw["limit_price"]).__name__ == "Decimal128"
    assert restored is not None
    assert restored.limit_price == Money.of("101.35")
    assert restored.created_at.tzinfo is not None


async def test_order_lookups_by_idempotency_key_ordertag_and_state(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    orders = repos.OrderRepository(migrated)
    order = RecordFactory().order(state="OPEN", session_date="2099-01-01")
    cleanup(orders, order)
    await orders.insert(order)

    assert await orders.get_by_idempotency_key(order.idempotency_key) == order
    assert await orders.get_by_ordertag(order.ordertag) == order
    assert await orders.in_states("2099-01-01", ["OPEN", "FILLED"]) == [order]
    assert await orders.in_states("2099-01-01", ["FILLED"]) == []


@pytest.mark.parametrize("field", ["idempotency_key", "ordertag"])
async def test_a_duplicate_order_raises_the_typed_error(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None], field: str
) -> None:
    orders = repos.OrderRepository(migrated)
    factory = RecordFactory()
    first = factory.order()
    second = factory.order(**{field: getattr(first, field)})
    cleanup(orders, first)
    cleanup(orders, second)
    await orders.insert(first)

    with pytest.raises(DuplicateRecordError) as raised:
        await orders.insert(second)

    assert raised.value.collection == "orders"
    assert raised.value.key_fields == (field,)


async def test_a_duplicate_broker_trade_id_raises_the_typed_error(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    executions = repos.ExecutionRepository(migrated)
    factory = RecordFactory()
    first = factory.execution()
    second = factory.execution(broker_trade_id=first.broker_trade_id)
    cleanup(executions, first)
    cleanup(executions, second)
    await executions.insert(first)

    with pytest.raises(DuplicateRecordError) as raised:
        await executions.insert(second)

    assert raised.value.key_fields == ("broker_trade_id",)
    assert await executions.get_by_broker_trade_id(first.broker_trade_id) == first
    assert await executions.for_order(first.order_id) == [first]


async def test_a_replace_that_collides_raises_the_typed_error(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    orders = repos.OrderRepository(migrated)
    factory = RecordFactory()
    first, second = factory.order(), factory.order()
    cleanup(orders, first)
    cleanup(orders, second)
    await orders.insert(first)
    await orders.insert(second)

    with pytest.raises(DuplicateRecordError):
        await orders.replace(second.model_copy(update={"ordertag": first.ordertag}))


async def test_position_lookups(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    positions = repos.PositionRepository(migrated)
    factory = RecordFactory()
    open_position = factory.position()
    flat = factory.position(account_id=open_position.account_id, net_quantity=0)
    cleanup(positions, open_position)
    cleanup(positions, flat)
    await positions.insert(open_position)
    await positions.insert(flat)

    found = await positions.get_for(open_position.account_id, open_position.instrument_id)
    assert found == open_position
    assert await positions.open_for_account(open_position.account_id) == [open_position]


async def test_kill_switch_is_a_single_upserted_document(migrated: Database) -> None:
    switch = repos.KillSwitchRepository(migrated)
    factory = RecordFactory()
    original = await switch.current()
    try:
        await switch.save(factory.kill_switch(halted=True))
        await switch.save(factory.kill_switch(halted=False))
        current = await switch.current()

        assert current is not None
        assert current.halted is False
        assert await switch.count() == 1
    finally:
        if original is None:
            await switch.delete(switch.CURRENT_ID)
        else:
            await switch.save(original)


async def test_named_lookups_for_the_remaining_repositories(
    migrated: Database, cleanup: Callable[[Repository[Any], Record], None]
) -> None:
    factory = RecordFactory()
    accounts, instruments = repos.AccountRepository(migrated), repos.InstrumentRepository(migrated)
    strategies, calendar = (
        repos.StrategyRepository(migrated),
        repos.MarketCalendarRepository(migrated),
    )
    commands, results = repos.CommandRepository(migrated), repos.CommandResultRepository(migrated)
    events, signals = repos.OrderEventRepository(migrated), repos.SignalRepository(migrated)
    account, instrument, strategy = factory.account(), factory.instrument(), factory.strategy()
    day, command = factory.market_calendar(), factory.command()
    result = factory.command_result(command.id)
    second_event, first_event = factory.order_event(seq=2), factory.order_event(seq=1)
    later_event = second_event.model_copy(update={"order_id": first_event.order_id})
    signal = factory.signal()
    for repo, record in [
        (accounts, account), (instruments, instrument), (strategies, strategy),
        (calendar, day), (commands, command), (results, result), (events, first_event),
        (events, later_event), (signals, signal),
    ]:  # fmt: skip
        cleanup(repo, record)
        await repo.insert(record)

    assert await accounts.get_by_client_code(account.client_code) == account
    assert await instruments.get_by_token("NSE", instrument.token) == instrument
    assert await instruments.get_by_symbol("NSE", instrument.tradingsymbol) == instrument
    assert await strategies.get_by_name(strategy.name) == strategy
    assert await calendar.get_by_date(day.date) == day
    assert await commands.get_by_idempotency_key(command.idempotency_key) == command
    assert command in await commands.with_status("PENDING")
    assert await results.for_command(command.id) == [result]
    assert await events.for_order(first_event.order_id) == [first_event, later_event]
    assert await signals.for_run(signal.strategy_run_id) == [signal]

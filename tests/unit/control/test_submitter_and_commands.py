"""A command is recorded once however often it is submitted, and only if it is valid."""

from datetime import timedelta

import pytest

from emporos.control.commands import (
    COMMAND_SCHEMAS,
    CommandStatus,
    CommandType,
    InvalidCommandError,
    parse_command,
)
from emporos.control.submitter import CommandSubmitter, IdempotencyConflictError
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import CommandRecord
from tests.support.records import NOW


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, CommandRecord] = {}

    async def insert(self, record: CommandRecord) -> None:
        if any(r.idempotency_key == record.idempotency_key for r in self.rows.values()):
            raise DuplicateRecordError("commands", "idempotency_key", ("idempotency_key",))
        self.rows[record.id] = record

    async def get_by_idempotency_key(self, key: str) -> CommandRecord | None:
        return next((r for r in self.rows.values() if r.idempotency_key == key), None)


def submitter(
    store: Store | None = None, ttl: timedelta = timedelta(minutes=10)
) -> CommandSubmitter:
    return CommandSubmitter(store or Store(), FixedClock(NOW), IdGenerator(), ttl)


VALID = {
    CommandType.SET_KILL_SWITCH: {"halted": True, "reason": "test"},
    CommandType.SQUARE_OFF_ALL: {},
    CommandType.CLOSE_POSITION: {"instrument_id": "NSE:3045"},
    CommandType.CANCEL_ORDER: {"order_id": "o1"},
    CommandType.PLACE_MANUAL_ORDER: {
        "instrument_id": "NSE:3045",
        "side": "BUY",
        "quantity": 1,
        "limit_price": "100.05",
        "reason": "test",
    },  # fmt: skip
    CommandType.START_STRATEGY: {"name": "momentum_v1"},
    CommandType.STOP_STRATEGY: {"name": "momentum_v1"},
    CommandType.UPDATE_STRATEGY_CONFIG: {"name": "momentum_v1", "config": {"a": "1"}},
    CommandType.TRIGGER_BACKFILL: {"instrument_ids": ["NSE:3045"], "days": 5},
    CommandType.RUN_BACKTEST: {
        "strategy": "momentum_v1",
        "start": "2026-01-01",
        "end": "2026-02-01",
    },
    CommandType.RECONCILE_NOW: {},
    CommandType.SET_TRADING_MODE: {"mode": "LIVE"},
}


def test_every_command_in_the_catalogue_has_a_schema_and_a_valid_example() -> None:
    assert set(VALID) == set(CommandType) == set(COMMAND_SCHEMAS)
    for kind, params in VALID.items():
        assert parse_command(kind.value, params)[0] is kind


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("NOT_A_COMMAND", {}),
        (
            CommandType.PLACE_MANUAL_ORDER,
            {**VALID[CommandType.PLACE_MANUAL_ORDER], "limit_price": 100.05},
        ),
        (
            CommandType.PLACE_MANUAL_ORDER,
            {**VALID[CommandType.PLACE_MANUAL_ORDER], "limit_price": "0"},
        ),
        (CommandType.PLACE_MANUAL_ORDER, {**VALID[CommandType.PLACE_MANUAL_ORDER], "quantity": 0}),
        (
            CommandType.PLACE_MANUAL_ORDER,
            {**VALID[CommandType.PLACE_MANUAL_ORDER], "quantity": "5"},
        ),
        (CommandType.PLACE_MANUAL_ORDER, {**VALID[CommandType.PLACE_MANUAL_ORDER], "side": "HOLD"}),
        (
            CommandType.PLACE_MANUAL_ORDER,
            {**VALID[CommandType.PLACE_MANUAL_ORDER], "order_type": "MARKET"},
        ),
        (CommandType.CANCEL_ORDER, {}),
        (CommandType.CANCEL_ORDER, {"order_id": ""}),
        (CommandType.SET_KILL_SWITCH, {"halted": "yes please"}),
        (CommandType.TRIGGER_BACKFILL, {"instrument_ids": [], "days": 5}),
    ],
)
def test_a_malformed_command_is_refused_at_the_door(kind: object, params: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(InvalidCommandError):
        parse_command(str(getattr(kind, "value", kind)), params)


class TestSubmitter:
    async def test_a_valid_command_is_recorded_pending_with_an_expiry(self) -> None:
        store = Store()
        record = await submitter(store).submit("k1", "CANCEL_ORDER", {"order_id": "o1"}, "rama")
        assert (record.status, record.issued_by, record.type) == ("PENDING", "rama", "CANCEL_ORDER")
        assert record.params == {"order_id": "o1"} and record.attempts == 0
        assert record.expires_at == NOW + timedelta(minutes=10)
        assert store.rows[record.id] == record and record.status == CommandStatus.PENDING

    async def test_a_double_submit_returns_the_first_command_and_creates_no_second(self) -> None:
        store = Store()
        first = await submitter(store).submit("k1", "CANCEL_ORDER", {"order_id": "o1"})
        again = await submitter(store).submit("k1", "CANCEL_ORDER", {"order_id": "o1"})
        assert again == first and len(store.rows) == 1

    async def test_the_same_key_for_a_different_command_is_a_client_bug_and_refused(self) -> None:
        store = Store()
        await submitter(store).submit("k1", "CANCEL_ORDER", {"order_id": "o1"})
        with pytest.raises(IdempotencyConflictError):
            await submitter(store).submit("k1", "CANCEL_ORDER", {"order_id": "o2"})
        with pytest.raises(IdempotencyConflictError):
            await submitter(store).submit("k1", "RECONCILE_NOW", {})
        assert len(store.rows) == 1

    async def test_nothing_is_recorded_for_an_invalid_command_or_a_blank_key(self) -> None:
        store = Store()
        with pytest.raises(InvalidCommandError):
            await submitter(store).submit("k1", "CANCEL_ORDER", {})
        with pytest.raises(ValueError, match="idempotency key"):
            await submitter(store).submit("  ", "RECONCILE_NOW", {})
        assert store.rows == {}

    def test_a_command_must_be_allowed_to_wait(self) -> None:
        with pytest.raises(ValueError):
            submitter(ttl=timedelta(0))

    def test_only_done_failed_rejected_and_expired_are_terminal(self) -> None:
        assert {s for s in CommandStatus if s.terminal} == {
            CommandStatus.DONE, CommandStatus.FAILED, CommandStatus.REJECTED, CommandStatus.EXPIRED,
        }  # fmt: skip

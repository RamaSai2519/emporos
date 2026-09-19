import asyncio
import json

import pytest

from emporos.observability.logging import (
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    clear_context()
    configure_logging("DEBUG")
    yield
    clear_context()


def test_log_record_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    logger = get_logger()
    logger.info("order_placed", order_id="o1")

    out = capsys.readouterr().out.strip()
    record = json.loads(out)

    assert record["event"] == "order_placed"
    assert record["order_id"] == "o1"
    assert record["level"] == "info"


def test_correlation_id_propagates_across_async_call_chain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def inner() -> None:
        get_logger().info("inner_event")

    async def outer() -> str:
        cid = bind_context()
        # create_task copies the current contextvars.Context at creation time,
        # so the bound correlation_id reaches the child task's log calls.
        await asyncio.create_task(inner())
        return cid

    correlation_id = asyncio.run(outer())

    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    inner_record = next(r for r in records if r["event"] == "inner_event")
    assert inner_record["correlation_id"] == correlation_id


def test_bind_context_uses_given_correlation_id() -> None:
    cid = bind_context(correlation_id="fixed-id", session_id="s1")
    assert cid == "fixed-id"


def test_clear_context_removes_bound_fields(capsys: pytest.CaptureFixture[str]) -> None:
    bind_context(correlation_id="should-not-appear")
    clear_context()

    get_logger().info("after_clear")

    record = json.loads(capsys.readouterr().out.strip())
    assert "correlation_id" not in record

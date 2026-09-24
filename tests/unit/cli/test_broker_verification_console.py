"""The operator's record/show view of the broker verification log (EM-186)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.cli.broker_verification_console import BrokerVerificationConsole, parse_outcome
from emporos.core.clock import FixedClock
from emporos.domain.broker_verification import CRITICAL_BROKER_CHECKS, CheckOutcome
from tests.support.broker_verification import InMemoryVerificationLog

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def console(clock: FixedClock | None = None) -> BrokerVerificationConsole:
    return BrokerVerificationConsole(
        InMemoryVerificationLog(), clock or FixedClock(NOW), timedelta(days=30)
    )


async def test_show_lists_every_critical_check_as_unverified_when_nothing_is_recorded() -> None:
    lines = await console().show()

    assert len(lines) == len(CRITICAL_BROKER_CHECKS)
    assert all("UNVERIFIED" in line for line in lines)


async def test_record_then_show_reports_the_outcome_and_evidence() -> None:
    c = console()
    await c.record(
        "static_ip_registered", "blocked", "docs/live-trading.md", "no static IP", "rama"
    )

    shown = await c.show()

    assert any(
        "static_ip_registered: BLOCKED" in line and "docs/live-trading.md" in line for line in shown
    )


async def test_extra_supporting_checks_are_shown_and_stale_results_are_flagged() -> None:
    clock = FixedClock(NOW)
    c = console(clock)
    await c.record("ws_frame_decoding", "pass", "tests/x.py", "d", "rama")
    clock.advance(timedelta(days=31))

    shown = await c.show()
    history = await c.show(history=True)

    assert any(
        "ws_frame_decoding: PASS" in line and "supporting" in line and "STALE" in line
        for line in shown
    )
    assert len(history) == 1


async def test_history_of_an_empty_log_says_so() -> None:
    assert await console().show(history=True) == ["nothing recorded"]


def test_outcome_names_are_parsed_and_unknown_ones_refused() -> None:
    assert parse_outcome("Unverified") is CheckOutcome.UNKNOWN
    assert parse_outcome("BLOCKED") is CheckOutcome.BLOCKED
    with pytest.raises(ValueError, match="not an outcome"):
        parse_outcome("maybe")

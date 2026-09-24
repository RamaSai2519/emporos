"""A broker check is a fact with an outcome and a time (EM-189)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.domain.broker_verification import CRITICAL_BROKER_CHECKS, BrokerCheck, CheckOutcome
from emporos.graduation.requirements import Assessment


def test_a_check_needs_a_name_and_an_aware_time() -> None:
    with pytest.raises(ValueError, match="name"):
        BrokerCheck(" ", CheckOutcome.PASS, None)
    with pytest.raises(ValueError, match="timezone"):
        BrokerCheck("login", CheckOutcome.PASS, datetime(2026, 9, 24))
    assert BrokerCheck("login", CheckOutcome.UNKNOWN, None).checked_at is None
    assert BrokerCheck("login", CheckOutcome.PASS, datetime(2026, 9, 24, tzinfo=UTC)).outcome


def test_the_critical_checks_are_named_once_each() -> None:
    assert len(set(CRITICAL_BROKER_CHECKS)) == len(CRITICAL_BROKER_CHECKS) > 0


def test_a_refusal_must_say_why() -> None:
    with pytest.raises(ValueError, match="why"):
        Assessment.refused("  ")

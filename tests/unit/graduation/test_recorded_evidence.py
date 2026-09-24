"""Recorded broker evidence stays fail-closed: only a recent recorded PASS counts (EM-186)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.broker_verification import (
    CRITICAL_BROKER_CHECKS,
    CheckOutcome,
    CheckResult,
)
from emporos.graduation.recorded_evidence import RecordedBrokerEvidence
from emporos.graduation.requirements import BrokerVerificationPassed
from tests.support.broker_verification import InMemoryVerificationLog

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def result(name: str, outcome: CheckOutcome, at: datetime = NOW) -> CheckResult:
    return CheckResult(name, outcome, at, "tests/x.py", "detail", "rama")


async def test_a_check_with_no_result_is_unknown_and_never_left_out() -> None:
    checks = await RecordedBrokerEvidence(InMemoryVerificationLog()).critical_checks()

    assert [c.name for c in checks] == list(CRITICAL_BROKER_CHECKS)
    assert all(c.outcome is CheckOutcome.UNKNOWN and c.checked_at is None for c in checks)


async def test_the_latest_result_wins_but_the_earlier_one_is_kept() -> None:
    log = InMemoryVerificationLog()
    await log.append(result("login_and_session", CheckOutcome.PASS, NOW))
    await log.append(result("login_and_session", CheckOutcome.FAIL, NOW + timedelta(hours=1)))

    checks = await RecordedBrokerEvidence(log).critical_checks()

    assert checks[0].outcome is CheckOutcome.FAIL
    assert len(await log.history("login_and_session")) == 2


@pytest.mark.parametrize("outcome", [CheckOutcome.BLOCKED, CheckOutcome.UNKNOWN, CheckOutcome.FAIL])
async def test_anything_but_pass_refuses_graduation(outcome: CheckOutcome) -> None:
    log = InMemoryVerificationLog()
    for name in CRITICAL_BROKER_CHECKS:
        await log.append(result(name, CheckOutcome.PASS))
    await log.append(result("static_ip_registered", outcome, NOW + timedelta(minutes=1)))
    requirement = BrokerVerificationPassed(
        RecordedBrokerEvidence(log), FixedClock(NOW), timedelta(days=30)
    )

    assessment = await requirement.assess(None)  # type: ignore[arg-type]

    assert not assessment.is_met
    assert assessment.refusal is not None
    assert "static_ip_registered" in assessment.refusal


async def test_all_recent_passes_are_met_and_stale_ones_are_not() -> None:
    log = InMemoryVerificationLog()
    for name in CRITICAL_BROKER_CHECKS:
        await log.append(result(name, CheckOutcome.PASS))
    fresh = BrokerVerificationPassed(
        RecordedBrokerEvidence(log), FixedClock(NOW + timedelta(days=1)), timedelta(days=30)
    )
    stale = BrokerVerificationPassed(
        RecordedBrokerEvidence(log), FixedClock(NOW + timedelta(days=31)), timedelta(days=30)
    )

    assert (await fresh.assess(None)).is_met  # type: ignore[arg-type]
    assert not (await stale.assess(None)).is_met  # type: ignore[arg-type]


def test_a_result_must_point_at_evidence_and_carry_a_zone() -> None:
    with pytest.raises(ValueError, match="evidence"):
        CheckResult("x", CheckOutcome.PASS, NOW, " ", "d", "rama")
    with pytest.raises(ValueError, match="timezone"):
        CheckResult("x", CheckOutcome.PASS, datetime(2026, 9, 24), "e", "d", "rama")

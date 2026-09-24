"""Broker verification evidence backed by the recorded results (EM-186).

For every critical check the latest recorded result is reported; a check with no result is
UNKNOWN with no time. Staleness is not decided here: `BrokerVerificationPassed` compares each
check's time to its maximum age. This class can only ever report what was recorded, so the
fail-closed behaviour of `UnverifiedBrokerEvidence` is preserved for anything not recorded.
"""

from __future__ import annotations

from emporos.domain.broker_verification import (
    CRITICAL_BROKER_CHECKS,
    BrokerCheck,
    BrokerVerificationLog,
    CheckOutcome,
)


class RecordedBrokerEvidence:
    def __init__(self, log: BrokerVerificationLog) -> None:
        self._log = log

    async def critical_checks(self) -> list[BrokerCheck]:
        checks: list[BrokerCheck] = []
        for name in CRITICAL_BROKER_CHECKS:
            latest = await self._log.latest(name)
            if latest is None:
                checks.append(
                    BrokerCheck(name, CheckOutcome.UNKNOWN, None, "no result recorded (EM-186)")
                )
            else:
                checks.append(latest.as_check())
        return checks

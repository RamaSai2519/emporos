"""The broker verification evidence used until EM-186 produces real evidence: nothing passes.

Every critical check is reported UNKNOWN with no time, so `BrokerVerificationPassed` refuses and
names each one. It is not a mock: it is the honest answer while no verification has been recorded,
and it can only ever refuse a promotion, never allow one.
"""

from __future__ import annotations

from emporos.domain.broker_verification import CRITICAL_BROKER_CHECKS, BrokerCheck, CheckOutcome


class UnverifiedBrokerEvidence:
    async def critical_checks(self) -> list[BrokerCheck]:
        return [
            BrokerCheck(
                name,
                CheckOutcome.UNKNOWN,
                None,
                "no broker verification has been recorded (EM-186)",
            )
            for name in CRITICAL_BROKER_CHECKS
        ]

"""Records and shows broker verification results (EM-186): the operator's view of the log.

It owns no storage and no clock: it is handed a `BrokerVerificationLog` and a `Clock`. Rendering is
plain lines so the same text serves the CLI and tests.
"""

from __future__ import annotations

from datetime import timedelta

from emporos.core.clock import Clock
from emporos.domain.broker_verification import (
    CRITICAL_BROKER_CHECKS,
    BrokerVerificationLog,
    CheckOutcome,
    CheckResult,
)

# What an operator may type. "unverified" is the plain-language name of UNKNOWN.
_OUTCOMES = {
    "pass": CheckOutcome.PASS,
    "fail": CheckOutcome.FAIL,
    "unverified": CheckOutcome.UNKNOWN,
    "unknown": CheckOutcome.UNKNOWN,
    "blocked": CheckOutcome.BLOCKED,
}


def parse_outcome(text: str) -> CheckOutcome:
    try:
        return _OUTCOMES[text.strip().lower()]
    except KeyError:
        raise ValueError(f"{text!r} is not an outcome; one of {', '.join(_OUTCOMES)}") from None


def _label(outcome: CheckOutcome) -> str:
    return "UNVERIFIED" if outcome is CheckOutcome.UNKNOWN else outcome.value.upper()


class BrokerVerificationConsole:
    def __init__(self, log: BrokerVerificationLog, clock: Clock, max_age: timedelta) -> None:
        self._log = log
        self._clock = clock
        self._max_age = max_age

    async def record(
        self, name: str, outcome: str, evidence_ref: str, detail: str, recorded_by: str
    ) -> list[str]:
        result = CheckResult(
            name, parse_outcome(outcome), self._clock.now(), evidence_ref, detail, recorded_by
        )
        await self._log.append(result)
        return [f"recorded {result.name}: {_label(result.outcome)} ({result.evidence_ref})"]

    async def show(self, *, history: bool = False) -> list[str]:
        if history:
            return [self._line(r) for r in await self._log.history()] or ["nothing recorded"]
        lines: list[str] = []
        recorded = {r.name for r in await self._log.history()}
        for name in [*CRITICAL_BROKER_CHECKS, *sorted(recorded - set(CRITICAL_BROKER_CHECKS))]:
            latest = await self._log.latest(name)
            critical = "critical" if name in CRITICAL_BROKER_CHECKS else "supporting"
            if latest is None:
                lines.append(f"{name} [{critical}]: UNVERIFIED (nothing recorded)")
            else:
                lines.append(f"{self._line(latest)} [{critical}]{self._stale(latest)}")
        return lines

    @staticmethod
    def _line(result: CheckResult) -> str:
        return (
            f"{result.name}: {_label(result.outcome)} at {result.checked_at.isoformat()} "
            f"by {result.recorded_by}; evidence {result.evidence_ref}; {result.detail}"
        )

    def _stale(self, result: CheckResult) -> str:
        if self._clock.now() - result.checked_at > self._max_age:
            return f" STALE (older than {self._max_age.days} days)"
        return ""

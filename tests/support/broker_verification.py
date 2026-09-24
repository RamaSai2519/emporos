"""An in-memory `BrokerVerificationLog` for tests: same Protocol as the Mongo one."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.domain.broker_verification import CheckResult


class InMemoryVerificationLog:
    def __init__(self) -> None:
        self._results: list[CheckResult] = []

    async def append(self, result: CheckResult) -> None:
        self._results.append(result)

    async def latest(self, name: str) -> CheckResult | None:
        matching = [r for r in self._results if r.name == name]
        return max(matching, key=lambda r: r.checked_at) if matching else None

    async def history(self, name: str | None = None) -> Sequence[CheckResult]:
        return sorted(
            (r for r in self._results if name is None or r.name == name),
            key=lambda r: r.checked_at,
        )

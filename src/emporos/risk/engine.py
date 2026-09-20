"""`RiskEngine` — every signal passes through here immediately before execution (plan.md §11).

    signal ─▶ snapshot ─▶ rule 1 ─▶ rule 2 ─▶ ... ─▶ RiskApprovedSignal
                              │ first block
                              ▼
                     RiskRejection ─▶ persisted to `risk_events` BEFORE it is returned

Rules run in the order given (the plan's order: cheap, system-wide guards first) and stop at the
first block. The engine FAILS CLOSED: a rule that raises, or a snapshot that cannot be built, is a
rejection — never an approval — and is persisted like any other. A rejection that cannot be
persisted raises `RiskAuditError`: an unauditable rejection is as bad as a silent drop, and the
signal is still not approved.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.errors import DefinitiveError
from emporos.core.ids import IdGenerator
from emporos.domain.signals import Signal
from emporos.risk.approval import (
    RiskApprovedSignal,
    RiskDecision,
    RiskRejection,
    RuleTrace,
    SealIssuer,
)
from emporos.risk.rules.base import RiskRule
from emporos.risk.snapshot import RiskSnapshot
from emporos.risk.verdict import RuleVerdict

logger = logging.getLogger(__name__)

SNAPSHOT_RULE = "RiskSnapshot"
RULE_ERROR_REASON = "the rule raised an error, which is treated as a block"


class RiskAuditError(DefinitiveError):
    """A rejection could not be written to `risk_events`."""


class SnapshotProvider(Protocol):
    async def snapshot(self, signal: Signal) -> RiskSnapshot: ...


class RejectionLog(Protocol):
    async def record(self, rejection: RiskRejection) -> None: ...


class RiskEngine:
    def __init__(
        self,
        rules: Sequence[RiskRule],
        snapshots: SnapshotProvider,
        rejections: RejectionLog,
        ids: IdGenerator,
        clock: Clock,
        alerts: AlertSink,
    ) -> None:
        names = [rule.name for rule in rules]
        if not names:
            raise ValueError("a risk engine with no rules would approve everything")
        if len(names) != len(set(names)):
            raise ValueError("two risk rules share a name")
        self._rules = tuple(rules)
        self._snapshots = snapshots
        self._rejections = rejections
        self._ids = ids
        self._clock = clock
        self._alerts = alerts
        self._issuer = SealIssuer()

    @property
    def rule_names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self._rules)

    async def review(self, signal: Signal, signal_id: str) -> RiskDecision:
        try:
            snapshot = await self._snapshots.snapshot(signal)
        except Exception as error:
            logger.exception("risk snapshot could not be built")
            return await self._reject(
                signal, signal_id, SNAPSHOT_RULE,
                RuleVerdict.block(
                    "the account and market state could not be read", error=type(error).__name__
                ),
                (), {},
            )  # fmt: skip
        trace: list[RuleTrace] = []
        for rule in self._rules:
            verdict = self._run(rule, signal, snapshot)
            trace.append(RuleTrace(rule.name, verdict.allowed, verdict.reason))
            if not verdict.allowed:
                return await self._reject(
                    signal, signal_id, rule.name, verdict, tuple(trace), snapshot.describe()
                )
        return RiskApprovedSignal(
            signal=signal,
            signal_id=signal_id,
            approval_id=self._ids.new_ulid(),
            approved_at=self._clock.now(),
            rules_passed=tuple(t.rule for t in trace),
            seal=self._issuer.issue(),
        )

    def _run(self, rule: RiskRule, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        try:
            return rule.evaluate(signal, snapshot)
        except Exception:
            logger.exception("risk rule raised", extra={"rule": rule.name})
            self._alerts.raise_alert("risk_rule_error", f"{rule.name} raised; signal blocked")
            return RuleVerdict.block(RULE_ERROR_REASON)

    async def _reject(
        self,
        signal: Signal,
        signal_id: str,
        rule: str,
        verdict: RuleVerdict,
        trace: tuple[RuleTrace, ...],
        snapshot: dict[str, object],
    ) -> RiskRejection:
        rejection = RiskRejection(
            signal=signal,
            signal_id=signal_id,
            rule=rule,
            reason=verdict.reason,
            details=verdict.details,
            rejected_at=self._clock.now(),
            trace=trace,
            snapshot=snapshot,
        )
        try:
            await self._rejections.record(rejection)
        except Exception as error:
            raise RiskAuditError(
                f"rejection by {rule} could not be persisted: {type(error).__name__}"
            ) from error
        return rejection

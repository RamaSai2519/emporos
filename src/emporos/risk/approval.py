"""The outcome of a risk review — and the ONLY thing execution will act on.

`RiskApprovedSignal` cannot be built without an `ApprovalSeal`, and only the risk engine holds the
means to make one (`emporos.risk.engine`). A raw `Signal` therefore has no route to execution:
`execution` accepts `RiskApprovedSignal` alone, and a test proves nothing else constructs one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from emporos.domain.signals import Signal


class ApprovalSeal:
    """Proof that the risk engine issued an approval. Issued by `SealIssuer` only."""

    __slots__ = ()

    def __init__(self, token: object) -> None:
        if token is not _ISSUER_TOKEN:
            raise TypeError("an approval seal can only be issued by the risk engine")


_ISSUER_TOKEN = object()


class SealIssuer:
    """Held by the risk engine. Anything that can call `issue` can approve a signal."""

    def issue(self) -> ApprovalSeal:
        return ApprovalSeal(_ISSUER_TOKEN)


@dataclass(frozen=True)
class RiskApprovedSignal:
    signal: Signal
    signal_id: str
    approval_id: str
    approved_at: datetime
    rules_passed: tuple[str, ...]
    seal: ApprovalSeal = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.seal, ApprovalSeal):
            raise TypeError("a signal is approved only by the risk engine")
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("approved_at must be timezone-aware UTC")
        if not self.rules_passed:
            raise ValueError("an approval must name the rules the signal passed")


@dataclass(frozen=True)
class RuleTrace:
    rule: str
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class RiskRejection:
    """Why a signal did not trade, with everything needed to replay the decision."""

    signal: Signal
    signal_id: str
    rule: str
    reason: str
    details: Mapping[str, str]
    rejected_at: datetime
    trace: tuple[RuleTrace, ...]
    snapshot: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


RiskDecision = RiskApprovedSignal | RiskRejection

"""What one rule says about one signal: allow it, or block it and say why."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class RuleVerdict:
    """A blocked verdict always carries a reason: an unexplained rejection is unauditable."""

    allowed: bool
    reason: str = ""
    details: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.allowed and not self.reason.strip():
            raise ValueError("a blocking verdict must say why")

    @classmethod
    def allow(cls) -> RuleVerdict:
        return cls(True)

    @classmethod
    def block(cls, reason: str, /, **details: object) -> RuleVerdict:
        return cls(False, reason, MappingProxyType({k: str(v) for k, v in details.items()}))

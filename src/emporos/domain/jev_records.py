"""The stored form of one Jev answer (EM-187).

Pure data. It lives in `domain`, not `jev`, because persistence must be able to store it and the
import contract forbids `persistence` from importing `emporos.jev`; `jev` translates its own
`JevDecision` into this at the boundary.

Only a decision the model actually gave is a record. A timeout or malformed reply carries no
answer to replay, and journalling it would let one transient failure stand forever against the
unique key: it is simply asked again the next time a run is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class JevDecisionRecord:
    request_hash: str  # of the payload sent: with prompt_hash and model, the record's identity
    as_of: datetime  # the market instant the question was about (never sent to the model)
    symbol: str  # as the model saw it: the pseudonym when the run was anonymised
    strategy: str
    mode: str  # the mode the recording run used; the ask itself is the same in every mode
    decision: str
    confidence: Decimal | None
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    tokens_used: int | None
    latency_ms: int
    recorded_at: datetime  # when the answer was produced

    def __post_init__(self) -> None:
        for name in ("request_hash", "symbol", "strategy", "decision", "provider", "model"):
            if not getattr(self, name):
                raise ValueError(f"a Jev decision record needs a {name}")
        if not self.prompt_version or not self.prompt_hash:
            raise ValueError("a Jev decision record needs the prompt version and hash")
        if self.as_of.tzinfo is None or self.recorded_at.tzinfo is None:
            raise ValueError("a Jev decision record's times must be timezone-aware")
        if self.confidence is not None and not (Decimal(0) <= self.confidence <= Decimal(1)):
            raise ValueError("confidence must be between 0 and 1")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.request_hash, self.prompt_hash, self.model)

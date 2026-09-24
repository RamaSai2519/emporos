"""What is known about whether a real broker connection is fit to trade real money (EM-189/186).

Graduation to live consumes this as evidence through `BrokerVerificationEvidence` and never sees how
a check was run. A check is a fact with an outcome and a time; anything not positively PASS, or
not recent enough, is not evidence that the broker works.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class CheckOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # never run, or could not be judged: not a pass


# The checks every live deployment must have passed (plan 2g). EM-186 owns running them; graduation
# owns refusing to go live while any of them is not a recent PASS.
CRITICAL_BROKER_CHECKS: tuple[str, ...] = (
    "login_and_session",
    "static_ip_registered",
    "limit_order_round_trip",
    "order_update_socket",
    "ordertag_round_trip",
    "order_rate_within_exchange_threshold",
    "algo_tagging_requirement_confirmed",
)


@dataclass(frozen=True)
class BrokerCheck:
    name: str
    outcome: CheckOutcome
    checked_at: datetime | None  # None: never run
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a broker check needs a name")
        if self.checked_at is not None and self.checked_at.tzinfo is None:
            raise ValueError("a broker check's time must be timezone-aware")


class BrokerVerificationEvidence(Protocol):
    async def critical_checks(self) -> Sequence[BrokerCheck]:
        """The latest result of every critical check. A check with no result is reported as
        UNKNOWN, never left out: a missing check must be visible as a missing check."""
        ...

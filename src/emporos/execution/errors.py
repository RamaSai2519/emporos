"""Why execution refused to act. All of them mean "nothing was sent to the broker"."""

from __future__ import annotations


class ExecutionRefusedError(ValueError):
    """Execution declined a request before any broker call; the intent (if any) is not sent."""


class InstrumentFrozenError(ExecutionRefusedError):
    """An order on this instrument is unresolved (UNKNOWN or still PENDING_NEW)."""


class ApprovalExpiredError(ExecutionRefusedError):
    """The risk approval is too old to act on: review the signal again against fresh state."""


class InvalidReplacementError(ExecutionRefusedError):
    """A repricing order that does not replace a confirmed-cancelled parent."""


class InvalidOrderPriceError(ExecutionRefusedError):
    """A price the exchange would refuse (not on the tick grid, not positive)."""

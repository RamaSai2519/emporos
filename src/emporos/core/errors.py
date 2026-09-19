"""Typed error hierarchy shared across all emporos packages.

Classification follows plan.md §22 (Phase 4 acceptance criteria) and the
`UNKNOWN`-order protocol in §12/§13: every error that crosses an I/O boundary
(broker transport, persistence, market data) must declare whether the action
that raised it definitely happened, definitely did not happen, or is unknown.
Callers branch on classification, never on error message text.
"""

from __future__ import annotations

from enum import Enum


class ErrorClassification(str, Enum):
    """How safe it is to treat the triggering action as having *not* happened."""

    RETRYABLE = "retryable"
    """The action did not take effect (e.g. rejected before reaching the broker). Safe to retry."""

    DEFINITIVE = "definitive"
    """The action's outcome is known and final (e.g. validation error, confirmed rejection)."""

    AMBIGUOUS = "ambiguous"
    """The outcome is unknown (timeout, connection reset, 5xx). Never retry blindly —
    resolve via an idempotent lookup (e.g. `find_orders_by_tag`, per Decision 7)."""


class EmporosError(Exception):
    """Base class for every emporos-raised exception."""

    classification: ErrorClassification = ErrorClassification.DEFINITIVE

    def __init__(self, message: str, *, classification: ErrorClassification | None = None) -> None:
        super().__init__(message)
        self.message = message
        if classification is not None:
            self.classification = classification


class RetryableError(EmporosError):
    classification = ErrorClassification.RETRYABLE


class DefinitiveError(EmporosError):
    classification = ErrorClassification.DEFINITIVE


class AmbiguousError(EmporosError):
    classification = ErrorClassification.AMBIGUOUS


class ConfigurationError(DefinitiveError):
    """Missing or invalid configuration. Never retryable — fix the input and restart."""

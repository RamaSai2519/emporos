"""Typed errors for the instrument-master pipeline."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.core.errors import DefinitiveError, RetryableError


class MasterDownloadError(RetryableError):
    """The upstream file could not be fetched (network failure or non-2xx). Try again later."""


class MasterFormatError(DefinitiveError):
    """The upstream body is not a JSON array of objects — a truncated or changed file."""


class MasterRejectedError(DefinitiveError):
    """A downloaded master failed validation and must not replace the current one."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("instrument master rejected: " + "; ".join(reasons))
        self.reasons = tuple(reasons)

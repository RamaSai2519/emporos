"""Alerting seam.

Components that must escalate a problem (a rejected instrument master, a stale
feed) depend on `AlertSink`; the composition root chooses the channel. Phase 14
adds real sinks (CloudWatch/SNS) — until then alerts are structured error logs.
"""

from __future__ import annotations

import logging
from typing import Protocol


class AlertSink(Protocol):
    def raise_alert(self, name: str, message: str) -> None: ...


class LogAlertSink:
    """Writes each alert as an ERROR log record."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("emporos.alerts")

    def raise_alert(self, name: str, message: str) -> None:
        self._logger.error("ALERT %s: %s", name, message)

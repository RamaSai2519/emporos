"""Structured JSON logging to stdout (plan.md §14).

Every log record carries `correlation_id` so a signal can be traced
end-to-end through risk, execution, fill, and P&L by one ID. Correlation
IDs are bound via `structlog.contextvars`, which `asyncio.create_task` and
friends propagate to child tasks by copying the current `contextvars.Context`
at creation time — so a call chain that fans out with `create_task` still
carries the correlation ID without threading it through every function
signature by hand.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from emporos.core.ids import IdGenerator


class Logging:
    """Process-wide structured logger: JSON to stdout with correlation context.

    One instance is created at the composition root with the process
    `IdGenerator`. It owns logging configuration, logger acquisition, and the
    `contextvars` correlation-id binding that async child tasks inherit.
    """

    def __init__(self, id_generator: IdGenerator) -> None:
        self._id_generator = id_generator

    def configure(self, level: str = "INFO") -> None:
        """Configure the stdout JSON pipeline. Call once at process startup."""
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=getattr(logging, level.upper(), logging.INFO),
        )
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
            cache_logger_on_first_use=True,
        )

    def get_logger(self) -> structlog.stdlib.BoundLogger:
        return cast(structlog.stdlib.BoundLogger, structlog.get_logger())

    def bind_context(self, *, correlation_id: str | None = None, **fields: str) -> str:
        """Bind correlation/session/order/etc IDs for every log call on this
        (async) call chain until `clear_context()`. Returns the correlation_id
        used (a fresh one is minted if none is given).
        """
        cid = correlation_id or self._id_generator.new_correlation_id()
        structlog.contextvars.bind_contextvars(correlation_id=cid, **fields)
        return cid

    def clear_context(self) -> None:
        structlog.contextvars.clear_contextvars()

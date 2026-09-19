"""Ordered, failure-isolated delivery of a value to registered handlers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")

_LOG = logging.getLogger(__name__)


class Fanout(Generic[T]):
    """Handlers run in registration order, synchronously; one that raises is logged and skipped
    so it can never starve the others (or the simulation that is publishing to it)."""

    def __init__(self, what: str) -> None:
        self._what = what
        self._handlers: list[Callable[[T], None]] = []

    def add(self, handler: Callable[[T], None]) -> None:
        self._handlers.append(handler)

    def publish(self, value: T) -> None:
        for handler in self._handlers:
            try:
                handler(value)
            except Exception:
                _LOG.exception("%s handler failed", self._what)

"""Lets consumers register tick handlers after the pipeline is built (`Broker.on_tick`)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from emporos.domain.ticks import Tick

_LOG = logging.getLogger(__name__)


class TickBroadcaster:
    """A pipeline `TickSubscriber` that forwards each tick to registered handlers, in
    registration order and synchronously. A failing handler is logged and skipped."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[Tick], None]] = []

    def add_handler(self, handler: Callable[[Tick], None]) -> None:
        self._handlers.append(handler)

    def on_tick(self, tick: Tick) -> None:
        for handler in self._handlers:
            try:
                handler(tick)
            except Exception:
                _LOG.exception("tick handler failed")

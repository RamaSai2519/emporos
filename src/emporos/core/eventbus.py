"""In-process, synchronous, typed event bus (Decision 11).

One deployable process, strict module boundaries, synchronous dispatch —
this gives total ordering for free, which matters because a fill must be
applied before the next signal is evaluated. A handler that raises is
isolated: it is logged and does not stop the publisher or other handlers
from running. (Per-handler wall-clock timeouts for strategy callbacks are
enforced by the strategy execution wrapper in `emporos.strategies`, not here.)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

EventT = TypeVar("EventT")
Handler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[EventT], handler: Callable[[EventT], None]) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[EventT], handler: Callable[[EventT], None]) -> None:
        self._handlers[event_type].remove(handler)

    def publish(self, event: object) -> None:
        for handler in list(self._handlers[type(event)]):
            try:
                handler(event)
            except Exception:
                logger.exception("event handler raised", extra={"event_type": type(event).__name__})

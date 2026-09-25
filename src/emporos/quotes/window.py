"""When quotes are recorded: the cash session, 09:15 to 15:30 IST on a weekday."""

from __future__ import annotations

from datetime import datetime, time

from emporos.core.clock import IST

__all__ = ["RecordingWindow"]


class RecordingWindow:
    """Open from `opens` (inclusive) to `closes` (exclusive) IST, Monday to Friday. An exchange
    holiday is not known here: a quote whose exchange time is not today is dropped by the
    recorder instead."""

    def __init__(self, opens: time = time(9, 15), closes: time = time(15, 30)) -> None:
        if opens >= closes:
            raise ValueError("the window must open before it closes")
        self._opens, self._closes = opens, closes

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(IST)
        return local.weekday() < 5 and self._opens <= local.time() < self._closes

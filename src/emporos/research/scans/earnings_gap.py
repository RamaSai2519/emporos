"""The overnight gap on an earnings-reaction day, held to the close (EM-191, cells
L2-earnings-gap-fade and L2-earnings-gap-continuation).

The rules are `RawGapRules` unchanged (gap of the reaction session's first bar against the
previous close, entry at the close of bar `entry_bar`, hold to the 15:15 square-off); the only
addition is the day filter: an entry is taken only on a session that reacts to the name's own
results filing (see `event_days`). Not parity-proven against an engine strategy, so advisory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from emporos.research.scans.base import IntradayScan, ScanExecution
from emporos.research.scans.event_days import EventReactionScan, InstrumentSymbols, SetOfDays
from emporos.research.scans.raw_gap import RawGapParameters, RawGapRules
from emporos.research.scans.regime_gate import DayGatedRules

__all__ = ["earnings_gap_scan"]


def earnings_gap_scan(
    parameters: RawGapParameters,
    execution: ScanExecution,
    events: Mapping[str, Sequence[datetime]],
    symbols: InstrumentSymbols,
) -> EventReactionScan:
    def inner(gate: SetOfDays) -> IntradayScan:
        return IntradayScan(
            "earnings_gap_hold_to_close",
            lambda: DayGatedRules(RawGapRules(parameters, execution.no_new_entries_after), gate),
            execution,
        )

    return EventReactionScan("earnings_gap_hold_to_close", inner, events, symbols)

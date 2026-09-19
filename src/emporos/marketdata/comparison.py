"""Compares our aggregated 1m candles against broker-reported ones (EM-54's Phase-5 acceptance:
"1m candles for a live watchlist match broker candles within tolerance across a full session").

Prices must agree within `price_tolerance` (rupees; default 1 tick = 0.05). Volume is compared
with a relative tolerance because tick-built volume can legitimately differ a little from the
exchange's. Minutes flagged `partial` are reported separately, never counted as mismatches: they
are known to cover a gap. The verdict needs a live session; this is the tool that renders it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.domain.money import Money


@dataclass(frozen=True)
class Mismatch:
    ts: datetime
    field: str
    ours: str
    broker: str


@dataclass(frozen=True)
class ComparisonReport:
    compared: int
    matched: int
    partial_skipped: int
    missing_from_ours: tuple[datetime, ...]
    missing_from_broker: tuple[datetime, ...]
    mismatches: tuple[Mismatch, ...]

    @property
    def agrees(self) -> bool:
        return not (self.missing_from_ours or self.mismatches)


ONE_TICK = Money.of("0.05")
DEFAULT_VOLUME_TOLERANCE = Decimal("0.02")


class CandleComparator:
    def __init__(
        self,
        price_tolerance: Money = ONE_TICK,
        volume_tolerance: Decimal = DEFAULT_VOLUME_TOLERANCE,
    ) -> None:
        self._price_tolerance = price_tolerance
        self._volume_tolerance = volume_tolerance

    def compare(self, ours: Sequence[Candle], broker: Sequence[Candle]) -> ComparisonReport:
        mine, theirs = {c.ts: c for c in ours}, {c.ts: c for c in broker}
        mismatches: list[Mismatch] = []
        matched = skipped = 0
        for ts in sorted(mine.keys() & theirs.keys()):
            if mine[ts].partial:
                skipped += 1
                continue
            found = self._differences(mine[ts], theirs[ts])
            mismatches.extend(found)
            matched += not found
        return ComparisonReport(
            compared=matched + len({m.ts for m in mismatches}),
            matched=matched,
            partial_skipped=skipped,
            missing_from_ours=tuple(sorted(theirs.keys() - mine.keys())),
            missing_from_broker=tuple(sorted(mine.keys() - theirs.keys())),
            mismatches=tuple(mismatches),
        )

    def _differences(self, ours: Candle, broker: Candle) -> list[Mismatch]:
        found = []
        for name in ("open", "high", "low", "close"):
            a, b = getattr(ours, name), getattr(broker, name)
            if abs(a.amount - b.amount) > self._price_tolerance.amount:
                found.append(Mismatch(ours.ts, name, str(a.amount), str(b.amount)))
        allowed = max(Decimal(broker.volume) * self._volume_tolerance, Decimal(1))
        if abs(Decimal(ours.volume) - Decimal(broker.volume)) > allowed:
            found.append(Mismatch(ours.ts, "volume", str(ours.volume), str(broker.volume)))
        return found

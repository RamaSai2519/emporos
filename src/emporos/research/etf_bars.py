"""Daily bars for the ETFs of the rotation cells, from the broker's history API (EM-233, A5).

The broker serves `ONE_DAY` candles, and a request that spans too much can be cut short without an
error, so a fetch is made in windows of 365 calendar days, each stored on arrival, and a full-year
window that comes back with far fewer than a year's sessions is FLAGGED (`sparse_windows`) for a
reader to judge: it is a listing year, a market holiday spell or a truncation, and the fetcher does
not tell which. The bars go to the cold tier through `ColdCandleArchive`, the same place every other
history goes, and the instruments are resolved from the broker's PUBLIC scrip master, so nothing is
read from Atlas.

The window list, the instrument resolution and the sparse-window rule are pure; the broker call is
behind `DailyCandleSource`, so the whole run is tested with a fake.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.persistence.candle_cold import ColdCandleArchive
from emporos.research.adjustments import AdjustmentLedger
from emporos.research.discontinuities import DiscontinuityAudit
from emporos.research.gap_classes import GapClassifier, IndexMoves

__all__ = [
    "ETF_SYMBOLS", "DailyCandleSource", "EtfBarAudit", "EtfBarFetcher", "EtfFetchReport",
    "EtfResolver", "fetch_windows",
]  # fmt: skip

ETF_SYMBOLS = ("NIFTYBEES-EQ", "JUNIORBEES-EQ", "GOLDBEES-EQ")
WINDOW_DAYS = 365
FULL_YEAR_MIN_BARS = 200  # a 365-day window normally holds about 245 sessions
_PAISE = Decimal(100)


class DailyCandleSource(Protocol):
    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class EtfResolver:
    """Trading symbols to `Instrument`s from the broker's public scrip master rows."""

    def resolve(
        self, rows: Sequence[Mapping[str, Any]], symbols: Sequence[str] = ETF_SYMBOLS
    ) -> list[Instrument]:
        found: list[Instrument] = []
        for symbol in symbols:
            matches = [r for r in rows if r.get("exch_seg") == "NSE" and r.get("symbol") == symbol]
            if len(matches) != 1:
                raise ValueError(f"{symbol}: {len(matches)} NSE rows in the scrip master (need 1)")
            row = matches[0]
            tick = Decimal(str(row.get("tick_size", "5"))) / _PAISE
            found.append(
                Instrument(
                    Exchange.NSE,
                    str(row["token"]),
                    symbol,
                    str(row.get("name", symbol)),
                    max(1, int(str(row.get("lotsize", "1")))),
                    Money(tick if tick > 0 else Decimal("0.05")),
                )  # fmt: skip
            )
        return found


def fetch_windows(
    first: date, last: date, days: int = WINDOW_DAYS
) -> list[tuple[datetime, datetime]]:
    """[start, end) windows of `days` days covering IST days `first..last`, oldest first."""
    if days < 1 or first > last:
        raise ValueError("a fetch needs a positive window and first <= last")
    out: list[tuple[datetime, datetime]] = []
    cursor = first
    while cursor <= last:
        end_day = min(cursor + timedelta(days=days), last + timedelta(days=1))
        out.append(
            (
                datetime.combine(cursor, time(0, 0), tzinfo=IST),
                datetime.combine(end_day, time(0, 0), tzinfo=IST),
            )
        )
        cursor = end_day
    return out


@dataclass(frozen=True)
class EtfFetchReport:
    instrument_id: str
    symbol: str
    bars: int
    first_day: date | None
    last_day: date | None
    windows: int
    sparse_windows: tuple[
        tuple[date, int], ...
    ]  # (window start, bars) of full-year windows short of 200


class EtfBarFetcher:
    def __init__(self, source: DailyCandleSource, archive: ColdCandleArchive) -> None:
        self._source = source
        self._archive = archive

    async def run(
        self, instruments: Sequence[Instrument], first: date, last: date
    ) -> list[EtfFetchReport]:
        windows = fetch_windows(first, last)
        reports: list[EtfFetchReport] = []
        for instrument in instruments:
            days: list[date] = []
            sparse: list[tuple[date, int]] = []
            for start, end in windows:
                candles = await self._source.fetch(instrument, Timeframe.D1, start, end)
                inside = [c for c in candles if start <= c.ts < end]
                await self._archive.archive(inside)
                days += [c.ts.astimezone(IST).date() for c in inside]
                if 0 < len(inside) < FULL_YEAR_MIN_BARS and end - start >= timedelta(
                    days=WINDOW_DAYS
                ):
                    sparse.append((start.astimezone(IST).date(), len(inside)))
            unique = sorted(set(days))
            reports.append(
                EtfFetchReport(
                    instrument.instrument_id,
                    instrument.tradingsymbol,
                    len(unique),
                    unique[0] if unique else None,
                    unique[-1] if unique else None,
                    len(windows),
                    tuple(sparse),
                )  # fmt: skip
            )
        return reports


class EtfBarAudit:
    """What the stored bars of one ETF look like: where they start and end, bars per year, the
    sessions of the reference index it has no bar for (only where the index has data), and every
    >= 15% open gap with its real-or-artifact class (a unit split is a split-shaped artifact)."""

    def __init__(self, index: IndexMoves, index_sessions: Sequence[date]) -> None:
        self._classifier = GapClassifier(index)
        self._index_sessions = sorted(index_sessions)

    def audit(self, report: EtfFetchReport, bars: Sequence[Candle]) -> dict[str, Any]:
        days = [b.ts.astimezone(IST).date() for b in bars]
        have = set(days)
        first = days[0] if days else None
        missing = (
            [d.isoformat() for d in self._index_sessions if first and d >= first and d not in have]
            if days
            else []
        )
        findings = DiscontinuityAudit(AdjustmentLedger()).audit(report.instrument_id, bars).findings
        verdicts = [self._classifier.classify(f) for f in findings]
        return {
            "symbol": report.symbol,
            "instrument_id": report.instrument_id,
            "bars": len(bars),
            "first_day": first.isoformat() if first else None,
            "last_day": days[-1].isoformat() if days else None,
            "bars_per_year": dict(sorted(Counter(d.year for d in days).items())),
            "sparse_windows": [[d.isoformat(), n] for d, n in report.sparse_windows],
            "gaps_against_nifty": missing,
            "discontinuities": [
                {
                    "day": v.finding.day.isoformat(),
                    "raw_ratio": f"{v.finding.raw_ratio:.4f}",
                    "class": v.gap_class.value,
                    "reason": v.reason,
                }
                for v in verdicts
            ],
            "gap_classes": dict(Counter(v.gap_class.value for v in verdicts)),
        }

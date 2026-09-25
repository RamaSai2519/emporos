"""What 5-minute bars and F&O bhavcopy we hold over the Track L window (EM-239, L-D3).

The expected sessions are the NIFTY 50's own 5-minute sessions in the window (the index trades
every session the exchange was open). A name's coverage is how many of those sessions carry at
least one of its bars, and how many carry a short day (fewer than `FULL_DAY_BARS` bars of the 75
in a regular session): an illiquid name has no bar for a 5-minute slot in which nothing traded, so
a short day is a fact about the name, not a hole. F&O coverage is read from the fetch ledger."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.market_context.bars import BarLoader

__all__ = ["BarCoverage", "FoCoverage", "NameCoverage", "coverage_lines"]

FULL_DAY_BARS = 70


@dataclass(frozen=True)
class NameCoverage:
    instrument_id: str
    label: str
    sessions: int
    expected: int
    short_sessions: int
    first_day: date | None
    last_day: date | None

    @property
    def share(self) -> float:
        return self.sessions / self.expected if self.expected else 0.0


def sessions_of(bars: Sequence[Candle]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for bar in bars:
        day = bar.ts.astimezone(IST).date()
        counts[day] = counts.get(day, 0) + 1
    return counts


class BarCoverage:
    def __init__(self, loader: BarLoader, reference_id: str) -> None:
        self._loader = loader
        self._reference = reference_id

    def expected_sessions(self, first: date, last: date) -> set[date]:
        return set(sessions_of(self._loader.load(self._reference, first, last)))

    def of(self, names: Mapping[str, str], first: date, last: date) -> list[NameCoverage]:
        """`names`: instrument id -> label."""
        expected = self.expected_sessions(first, last)
        out: list[NameCoverage] = []
        for instrument_id, label in sorted(names.items(), key=lambda kv: kv[1]):
            counts = {
                d: n
                for d, n in sessions_of(self._loader.load(instrument_id, first, last)).items()
                if d in expected
            }
            days = sorted(counts)
            out.append(
                NameCoverage(
                    instrument_id,
                    label,
                    len(counts),
                    len(expected),
                    sum(1 for n in counts.values() if n < FULL_DAY_BARS),
                    days[0] if days else None,
                    days[-1] if days else None,
                )  # fmt: skip
            )
        return out


@dataclass(frozen=True)
class FoCoverage:
    """The F&O bhavcopy days on disk (from the fetch ledger) over the window."""

    fetched: int
    absent: int
    missing: tuple[date, ...]  # expected sessions with no ledger line at all
    contracts: str

    @staticmethod
    def of(ledger: Path, expected: Iterable[date], first: date, last: date) -> FoCoverage:
        fetched: set[date] = set()
        absent: set[date] = set()
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            day = date.fromisoformat(row["day"])
            if first <= day <= last:
                (fetched if row["outcome"] == "fetched" else absent).add(day)
        missing = tuple(sorted(d for d in expected if d not in fetched and d not in absent))
        return FoCoverage(
            len(fetched), len(absent), missing,
            "index futures and options only (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50): "
            "the parser drops stock-derivative rows, so no stock option premium or open interest",
        )  # fmt: skip


def coverage_lines(
    names: Sequence[NameCoverage], expected: int, fo: FoCoverage, first: date, last: date
) -> list[str]:
    full = [n for n in names if n.share >= 0.99]
    partial = [n for n in names if 0 < n.share < 0.99]
    none = [n for n in names if n.sessions == 0]
    lines = [
        f"5-minute bars over {first}..{last}: {expected} NIFTY sessions expected, "
        f"{len(names)} names",
        f"  full (>= 99% of sessions): {len(full)}; partial: {len(partial)}; none: {len(none)}",
    ]
    lines += [
        f"  partial  {n.label:<14} {n.sessions}/{n.expected} sessions ({n.share:.0%}), "
        f"{n.first_day}..{n.last_day}, {n.short_sessions} short days"
        for n in partial
    ]
    lines += [f"  none     {n.label}" for n in none]
    short = sorted(names, key=lambda n: -n.short_sessions)[:8]
    lines.append(
        "  most short days (< 70 bars): "
        + ", ".join(f"{n.label} {n.short_sessions}" for n in short)
    )
    lines += [
        "",
        f"F&O bhavcopy over the same window: {fo.fetched} days on disk, "
        f"{fo.absent} recorded absent (holidays), "
        f"{len(fo.missing)} expected sessions with no record",
        f"  what it holds: {fo.contracts}",
    ]
    if fo.missing:
        lines.append("  no record: " + ", ".join(d.isoformat() for d in fo.missing[:20]))
    return lines

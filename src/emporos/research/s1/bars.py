"""5-minute OHLCV bars by session, as plain float arrays, for the S1 simulator (EM-219).

`DayBars` is one instrument's one session. `BarStore` is the seam: production reads the vault-guarded,
split-adjusted archive (composed in `emporos.cli`) through `CandleBarStore` and a per-name cache;
tests hand in a dictionary. The ATR at an entry looks back over the session's bars so far and, if
there are fewer than 14, the previous session's tail."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import numpy as np

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.atlas.arrays import Floats, Ints

__all__ = ["BarStore", "CandleBarStore", "DayBars", "MemoryBarStore", "atr_before"]

ATR_BARS = 14
BAR_MINUTES = 5


@dataclass(frozen=True)
class DayBars:
    closes_at: Ints  # minutes after midnight IST at which each bar ends
    open: Floats
    high: Floats
    low: Floats
    close: Floats
    volume: Floats

    def __len__(self) -> int:
        return len(self.closes_at)

    def index_ending_at(self, minute: int) -> int | None:
        """The bar that ends at `minute`, or None."""
        found = np.flatnonzero(self.closes_at == minute)
        return int(found[0]) if len(found) else None


class BarStore(Protocol):
    def day(self, instrument_id: str, day: date) -> DayBars | None: ...

    def previous(self, instrument_id: str, day: date) -> DayBars | None:
        """The last session before `day` that has bars."""
        ...


class MemoryBarStore:
    def __init__(self, bars: Mapping[tuple[str, date], DayBars]) -> None:
        self._bars = dict(bars)
        self._days: dict[str, list[date]] = {}
        for instrument_id, day in self._bars:
            self._days.setdefault(instrument_id, []).append(day)
        for days in self._days.values():
            days.sort()

    def day(self, instrument_id: str, day: date) -> DayBars | None:
        return self._bars.get((instrument_id, day))

    def previous(self, instrument_id: str, day: date) -> DayBars | None:
        earlier = [d for d in self._days.get(instrument_id, []) if d < day]
        return self._bars[(instrument_id, earlier[-1])] if earlier else None


def atr_before(store: BarStore, instrument_id: str, day: date, upto: int) -> float:
    """The mean true range of the last 14 bars that ended by minute `upto` (0.0 with fewer than 2
    bars to go on)."""
    today = store.day(instrument_id, day)
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    if today is not None:
        n = int(np.searchsorted(today.closes_at, upto, side="right"))
        highs, lows, closes = list(today.high[:n]), list(today.low[:n]), list(today.close[:n])
    if len(closes) < ATR_BARS + 1:
        before = store.previous(instrument_id, day)
        if before is not None:
            highs = list(before.high) + highs
            lows = list(before.low) + lows
            closes = list(before.close) + closes
    if len(closes) < 2:
        return 0.0
    h, low, c = np.array(highs[-ATR_BARS:]), np.array(lows[-ATR_BARS:]), np.array(closes)
    previous = c[-len(h) - 1 : -1]
    tr = np.maximum(h - low, np.maximum(np.abs(h - previous), np.abs(low - previous)))
    return float(tr.mean())


class CandleBarStore:
    """Sessions built from candles, loaded a name at a time and kept."""

    def __init__(self, loader: CandleRange, first: date, last: date) -> None:
        self._loader, self._first, self._last = loader, first, last
        self._held: dict[str, dict[date, DayBars]] = {}

    def _name(self, instrument_id: str) -> dict[date, DayBars]:
        if instrument_id not in self._held:
            self._held[instrument_id] = sessions_from(
                self._loader.load(instrument_id, self._first, self._last)
            )
        return self._held[instrument_id]

    def day(self, instrument_id: str, day: date) -> DayBars | None:
        return self._name(instrument_id).get(day)

    def previous(self, instrument_id: str, day: date) -> DayBars | None:
        held = self._name(instrument_id)
        earlier = [d for d in held if d < day]
        return held[max(earlier)] if earlier else None


class CandleRange(Protocol):
    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]: ...


def sessions_from(candles: Sequence[Candle]) -> dict[date, DayBars]:
    by_day: dict[date, list[Candle]] = {}
    for candle in sorted(candles, key=lambda c: c.ts):
        by_day.setdefault(candle.ts.astimezone(IST).date(), []).append(candle)
    return {day: _day_bars(bars) for day, bars in by_day.items()}


def _day_bars(bars: Sequence[Candle]) -> DayBars:
    def minute(c: Candle) -> int:
        local = c.ts.astimezone(IST)
        return local.hour * 60 + local.minute + BAR_MINUTES

    return DayBars(
        np.array([minute(c) for c in bars], dtype=np.int64),
        np.array([float(c.open.amount) for c in bars]),
        np.array([float(c.high.amount) for c in bars]),
        np.array([float(c.low.amount) for c in bars]),
        np.array([float(c.close.amount) for c in bars]),
        np.array([float(c.volume) for c in bars]),
    )


def save_sessions(path: Path, sessions: Mapping[date, DayBars]) -> None:
    """One name's sessions as one `.npz` (dates as ordinals, the arrays concatenated)."""
    days = sorted(sessions)
    lengths = [len(sessions[d]) for d in days]
    np.savez(
        path,
        days=np.array([d.toordinal() for d in days], dtype=np.int64),
        lengths=np.array(lengths, dtype=np.int64),
        closes_at=np.concatenate([sessions[d].closes_at for d in days]),
        **{
            name: np.concatenate([getattr(sessions[d], name) for d in days])
            for name in ("open", "high", "low", "close", "volume")
        },
    )


def load_sessions(path: Path) -> dict[date, DayBars]:
    with np.load(path) as held:
        edges = np.concatenate([[0], np.cumsum(held["lengths"])])
        out: dict[date, DayBars] = {}
        for i, ordinal in enumerate(held["days"]):
            sl = slice(int(edges[i]), int(edges[i + 1]))
            out[date.fromordinal(int(ordinal))] = DayBars(
                held["closes_at"][sl], held["open"][sl], held["high"][sl], held["low"][sl],
                held["close"][sl], held["volume"][sl],
            )  # fmt: skip
        return out

"""Corporate-action adjustment for daily bars: the factor ledger and the adjuster (EM-221, A-F2).

Signals read ADJUSTED prices, so a multi-day return does not cross a split or bonus as if it were a
crash. Fills use RAW prices, the ones the exchange traded at. The two never mix: `PriceAdjuster`
returns both series, bar for bar, and the raw bars are the caller's own objects, untouched.

`AdjustmentFactor.ratio` is the price multiplier for every bar BEFORE `ex_date`: the price after the
action over the price before it. A 1:5 split is 0.2; a 1:1 bonus is 0.5; a 2:3 bonus is 0.6. A bar
on or after `ex_date` is on the new basis already. Several factors on one instrument multiply.
`ex_date` is the first session ON THE NEW PRICE BASIS: the exchange's ex-date where the series is
raw at the action, or, for a series the broker has already adjusted retroactively, the day its
history switches from the raw basis to the adjusted one (`gap_matching`). A factor dated on a
holiday works: it scales the bars before that date.

Volume is scaled the other way (a 1:5 split turns 100 old shares into 500 new ones), rounded half
to even, so a traded value (price x volume) survives the adjustment to within a share.

Dividends are NOT adjusted: a known limit (a cash dividend is a small step, and a long-only
delivery rule still receives it). Every factor names its `source`: a ledger entry nobody can trace
is refused, because an adjustment is a claim about a company and someone has to have made it.
The ledger's file is data, filled from a source the operator has confirmed; this module fetches
nothing.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle
from emporos.domain.money import Money

__all__ = [
    "ActionKind", "AdjustedSeries", "AdjustmentFactor", "AdjustmentLedger", "DEFAULT_LEDGER",
    "PriceAdjuster",
]  # fmt: skip

DEFAULT_LEDGER = Path("config/universe/d1/adjustments.yaml")


class ActionKind(StrEnum):
    SPLIT = "split"
    BONUS = "bonus"
    OTHER = "other"  # a demerger, a consolidation: whatever moves the price basis and is not either


@dataclass(frozen=True)
class AdjustmentFactor:
    instrument_id: str
    ex_date: date
    ratio: Decimal  # price multiplier for bars before ex_date; > 0 and never 1
    kind: ActionKind
    source: str  # who said so: a filing, a vendor file, a broker record
    note: str = ""

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("a factor names its instrument")
        if not self.source.strip():
            raise ValueError(f"{self.instrument_id} {self.ex_date}: a factor needs a source")
        if not self.ratio.is_finite() or self.ratio <= 0:
            raise ValueError(f"{self.instrument_id} {self.ex_date}: a ratio is a positive number")
        if self.ratio == 1:
            raise ValueError(f"{self.instrument_id} {self.ex_date}: a ratio of 1 adjusts nothing")


class AdjustmentLedger:
    """Every known factor, per instrument, oldest ex-date first. Immutable once built."""

    def __init__(self, factors: Iterable[AdjustmentFactor] = ()) -> None:
        seen: set[tuple[str, date, Decimal, ActionKind]] = set()
        by_instrument: dict[str, list[AdjustmentFactor]] = defaultdict(list)
        for factor in factors:
            key = (factor.instrument_id, factor.ex_date, factor.ratio, factor.kind)
            if key in seen:
                raise ValueError(
                    f"{factor.instrument_id} {factor.ex_date}: the same factor is listed twice"
                )
            seen.add(key)
            by_instrument[factor.instrument_id].append(factor)
        self._by_instrument = {
            i: tuple(sorted(fs, key=lambda f: (f.ex_date, f.ratio)))
            for i, fs in by_instrument.items()
        }

    def __len__(self) -> int:
        return sum(len(fs) for fs in self._by_instrument.values())

    @property
    def instrument_ids(self) -> frozenset[str]:
        return frozenset(self._by_instrument)

    def factors_for(self, instrument_id: str) -> tuple[AdjustmentFactor, ...]:
        return self._by_instrument.get(instrument_id, ())

    @property
    def content_hash(self) -> str:
        """Names the ledger a result used: a factor added later is a different ledger."""
        document = [
            [f.instrument_id, f.ex_date.isoformat(), str(f.ratio), f.kind.value, f.source]
            for fs in self._by_instrument.values()
            for f in fs
        ]
        return hashlib.sha256(json.dumps(sorted(document)).encode()).hexdigest()

    @classmethod
    def load(cls, path: Path = DEFAULT_LEDGER) -> AdjustmentLedger:
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            unknown = set(raw) - {"factors"}
            if unknown:
                raise ValueError(f"unknown keys {sorted(unknown)}")
            return cls(cls._factor(entry) for entry in raw.get("factors") or [])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigurationError(f"{path} is not an adjustment ledger: {error}") from error

    @staticmethod
    def _factor(entry: Mapping[str, Any]) -> AdjustmentFactor:
        unknown = set(entry) - {"instrument_id", "ex_date", "ratio", "kind", "source", "note"}
        if unknown:
            raise ValueError(f"unknown keys {sorted(unknown)} in {dict(entry)}")
        ratio = entry["ratio"]
        if not isinstance(ratio, str):  # a YAML float would already have lost exactness
            raise ValueError(f"a ratio is a quoted string, got {ratio!r}")
        try:
            value = Decimal(ratio)
        except InvalidOperation:
            raise ValueError(f"{ratio!r} is not a number") from None
        return AdjustmentFactor(
            str(entry["instrument_id"]),
            date.fromisoformat(str(entry["ex_date"])),
            value,
            ActionKind(str(entry["kind"])),
            str(entry["source"]),
            str(entry.get("note", "")),
        )

    def save(self, path: Path) -> None:
        document = {
            "factors": [
                {
                    "instrument_id": f.instrument_id,
                    "ex_date": f.ex_date.isoformat(),
                    "ratio": str(f.ratio),
                    "kind": f.kind.value,
                    "source": f.source,
                    **({"note": f.note} if f.note else {}),
                }
                for i in sorted(self._by_instrument)
                for f in self._by_instrument[i]
            ]
        }
        path.write_text(yaml.safe_dump(document, sort_keys=False, width=120), encoding="utf-8")


@dataclass(frozen=True)
class AdjustedSeries:
    """One instrument's daily bars on two bases. `raw[i]` and `adjusted[i]` are the same session."""

    raw: tuple[Candle, ...]
    adjusted: tuple[Candle, ...]
    cumulative: tuple[Decimal, ...]  # the multiplier applied to each session's prices

    def __post_init__(self) -> None:
        if not len(self.raw) == len(self.adjusted) == len(self.cumulative):
            raise ValueError("the two series and the multipliers cover the same sessions")


class PriceAdjuster:
    """Raw daily bars and a ledger to the two series. Pure."""

    def __init__(self, ledger: AdjustmentLedger) -> None:
        self._ledger = ledger

    def adjust(self, instrument_id: str, raw: Sequence[Candle]) -> AdjustedSeries:
        days = [bar.ts.astimezone(IST).date() for bar in raw]
        if any(bar.instrument_id != instrument_id for bar in raw):
            raise ValueError("one instrument's bars at a time")
        if any(later <= earlier for earlier, later in itertools.pairwise(days)):
            raise ValueError("daily bars must be oldest first, one per session")
        factors = self._ledger.factors_for(instrument_id)
        multipliers = [self._multiplier(day, factors) for day in days]
        adjusted = tuple(self._scaled(bar, m) for bar, m in zip(raw, multipliers, strict=True))
        return AdjustedSeries(tuple(raw), adjusted, tuple(multipliers))

    @staticmethod
    def _multiplier(day: date, factors: Sequence[AdjustmentFactor]) -> Decimal:
        product = Decimal(1)
        for factor in factors:
            if day < factor.ex_date:
                product *= factor.ratio
        return product

    @staticmethod
    def _scaled(bar: Candle, multiplier: Decimal) -> Candle:
        if multiplier == 1:
            return bar
        volume = (Decimal(bar.volume) / multiplier).to_integral_value(rounding=ROUND_HALF_EVEN)
        return Candle(
            bar.instrument_id,
            bar.timeframe,
            bar.ts,
            Money(bar.open.amount * multiplier),
            Money(bar.high.amount * multiplier),
            Money(bar.low.amount * multiplier),
            Money(bar.close.amount * multiplier),
            int(volume),
            bar.partial,
        )

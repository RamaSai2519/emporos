"""The D1 research universe: which of the 250 wider-universe names a cell may screen (EM-214).

Three decisions, each made once and committed (`config/universe/d1/universe.yaml`), so a cell's
universe is a fact in git and not something a run recomputes:

* **Holdout.** A seeded 30% of the D1 names outside the audited 29 are set aside and never read by
  any research read. The 29 have been looked at for months; the rest have not, and the plan's
  instrument-sealed vault (§4.3) needs names no earlier work has loaded. The choice is a hash of
  `seed:instrument id`, so it depends on the names and the seed only: it cannot be steered by a
  price, and the same seed gives the same set on any machine.
* **Liquidity and history.** From Discovery bars only: at least `min_sessions` sessions and a median
  daily traded value (sum of close x volume) of at least `min_median_daily_value`. Fixed in code
  before the profile was looked at.
* **Corporate actions.** An open at least 15% away from the previous session's close is an
  unadjusted split, bonus or bad print (the audit's own limit): that instrument-day is quarantined
  and no trade is taken on it. Actions smaller than that (a 1:10 bonus is a 9% step) are NOT caught;
  a known limit of raw prices, declared here.

`D1UniverseBuilder` never asks for a held-out name's bars (a test proves it), and `D1Universe` is a
`ScreenUniverse`: the seam `CellScreenRun` takes in place of the 29-name audit.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle
from emporos.research.cell_run import BarSource
from emporos.research.partition import DISCOVERY, DataSplit

__all__ = [
    "DEFAULT_MANIFEST", "D1Manifest", "D1Profile", "D1Profiler", "D1Universe", "D1UniverseBuilder",
    "LiquidityRule", "SeededHoldout",
]  # fmt: skip

DEFAULT_MANIFEST = Path("config/universe/d1/universe.yaml")
DISCONTINUITY_LIMIT = Decimal("0.15")  # the history audit's own limit (history.quality)
_CRORE = Decimal(10_000_000)


@dataclass(frozen=True)
class D1Profile:
    """What Discovery bars say about one name, and nothing about what it went on to return."""

    instrument_id: str
    sessions: int
    median_daily_value: Decimal  # rupees traded in a session (close x volume, summed)
    discontinuities: tuple[date, ...]  # sessions whose open is >= 15% from the last close


class D1Profiler:
    """Bars to a `D1Profile`. Pure: the caller decides which bars, and so which split."""

    def __init__(self, limit: Decimal = DISCONTINUITY_LIMIT) -> None:
        self._limit = limit

    def profile(self, instrument_id: str, bars: Sequence[Candle]) -> D1Profile:
        sessions: dict[date, list[Candle]] = defaultdict(list)
        for bar in bars:
            sessions[bar.ts.astimezone(IST).date()].append(bar)
        ordered = sorted(sessions)
        values = [
            sum((b.close.amount * b.volume for b in sessions[day]), Decimal(0)) for day in ordered
        ]
        return D1Profile(
            instrument_id,
            len(ordered),
            Decimal(median(values)) if values else Decimal(0),
            tuple(self._discontinuities(ordered, sessions)),
        )

    def _discontinuities(
        self, ordered: Sequence[date], sessions: Mapping[date, Sequence[Candle]]
    ) -> Iterable[date]:
        for previous, current in pairwise(ordered):
            last_close = max(sessions[previous], key=lambda b: b.ts).close.amount
            first_open = min(sessions[current], key=lambda b: b.ts).open.amount
            if last_close > 0 and abs(first_open / last_close - 1) >= self._limit:
                yield current


@dataclass(frozen=True)
class LiquidityRule:
    min_median_daily_value: Decimal = 10 * _CRORE  # Rs 10 crore a session: 2,000x a Rs 50,000 order
    min_sessions: int = 500  # two years of Discovery: enough for a name to matter to a trade count

    def reason_to_exclude(self, profile: D1Profile) -> str | None:
        if profile.sessions < self.min_sessions:
            return f"{profile.sessions} sessions in Discovery (need {self.min_sessions})"
        if profile.median_daily_value < self.min_median_daily_value:
            crore = profile.median_daily_value / _CRORE
            need = self.min_median_daily_value / _CRORE
            return f"median daily value Rs {crore:.1f} crore (need {need})"
        return None


class SeededHoldout:
    """Picks a fraction of names by hashing `seed:id`: a function of the names and the seed only."""

    def __init__(self, seed: str, fraction: Decimal) -> None:
        if not seed.strip():
            raise ValueError("a holdout needs a seed")
        if not Decimal(0) < fraction < Decimal(1):
            raise ValueError("a holdout fraction is strictly between 0 and 1")
        self._seed = seed
        self._fraction = fraction

    def pick(self, candidates: Iterable[str]) -> frozenset[str]:
        unique = sorted(set(candidates))
        count = int((self._fraction * len(unique)).to_integral_value())
        ranked = sorted(unique, key=lambda i: hashlib.sha256(f"{self._seed}:{i}".encode()).digest())
        return frozenset(ranked[:count])


@dataclass(frozen=True)
class D1Manifest:
    seed: str
    holdout_fraction: Decimal
    liquidity: LiquidityRule
    profiled: DataSplit
    audited: tuple[str, ...]
    holdout: tuple[str, ...]  # never read by research
    included: tuple[str, ...]
    excluded: Mapping[str, str]  # id -> why
    quarantined: tuple[tuple[str, date], ...]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_document(), sort_keys=True, default=str).encode()
        ).hexdigest()

    @property
    def holdout_hash(self) -> str:
        return hashlib.sha256(",".join(sorted(self.holdout)).encode()).hexdigest()

    def to_document(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "holdout_fraction": str(self.holdout_fraction),
            "liquidity": {
                "min_median_daily_value": str(self.liquidity.min_median_daily_value),
                "min_sessions": self.liquidity.min_sessions,
            },
            "profiled": {
                "split": self.profiled.name,
                "first": self.profiled.first.isoformat(),
                "last": self.profiled.last.isoformat(),
            },
            "audited": list(self.audited),
            "holdout": sorted(self.holdout),
            "included": list(self.included),
            "excluded": dict(sorted(self.excluded.items())),
            "quarantined": [[i, d.isoformat()] for i, d in self.quarantined],
        }

    def save(self, path: Path) -> None:
        document = {**self.to_document(), "content_hash": self.content_hash}
        path.write_text(yaml.safe_dump(document, sort_keys=False, width=120), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> D1Manifest:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifest = cls(
                seed=str(raw["seed"]),
                holdout_fraction=Decimal(str(raw["holdout_fraction"])),
                liquidity=LiquidityRule(
                    Decimal(str(raw["liquidity"]["min_median_daily_value"])),
                    int(raw["liquidity"]["min_sessions"]),
                ),
                profiled=DataSplit(
                    str(raw["profiled"]["split"]),
                    date.fromisoformat(str(raw["profiled"]["first"])),
                    date.fromisoformat(str(raw["profiled"]["last"])),
                ),
                audited=tuple(raw["audited"]),
                holdout=tuple(raw["holdout"]),
                included=tuple(raw["included"]),
                excluded=dict(raw["excluded"]),
                quarantined=tuple(
                    (str(i), date.fromisoformat(str(d))) for i, d in raw["quarantined"]
                ),
            )
            if raw["content_hash"] != manifest.content_hash:
                raise ValueError(
                    "content_hash does not match the body: the file was edited by hand"
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigurationError(f"{path} is not a D1 universe manifest: {error}") from error
        if set(manifest.included) & set(manifest.holdout):
            raise ConfigurationError(f"{path}: a held-out name is in the research universe")
        return manifest


class D1UniverseBuilder:
    def __init__(
        self,
        seed: str,
        holdout_fraction: Decimal,
        liquidity: LiquidityRule,
        profiler: D1Profiler,
        split: DataSplit = DISCOVERY,
    ) -> None:
        self._holdout = SeededHoldout(seed, holdout_fraction)
        self._seed = seed
        self._fraction = holdout_fraction
        self._liquidity = liquidity
        self._profiler = profiler
        self._split = split

    def build(
        self,
        candidates: Sequence[str],
        audited: Sequence[str],
        audit_quarantined: Iterable[tuple[str, date]],
        bars: BarSource,
    ) -> D1Manifest:
        """`bars` is asked for the research names only: a held-out name's bars are never read."""
        held = self._holdout.pick(i for i in candidates if i not in set(audited))
        included: list[str] = []
        excluded: dict[str, str] = {}
        quarantined: set[tuple[str, date]] = set(audit_quarantined)
        for instrument_id in sorted(set(candidates) - held):
            profile = self._profiler.profile(instrument_id, bars.bars(instrument_id, self._split))
            reason = self._liquidity.reason_to_exclude(profile)
            if reason is not None:
                excluded[instrument_id] = reason
                continue
            included.append(instrument_id)
            quarantined.update((instrument_id, day) for day in profile.discontinuities)
        return D1Manifest(
            self._seed, self._fraction, self._liquidity, self._split, tuple(sorted(audited)),
            tuple(sorted(held)), tuple(included), excluded, tuple(sorted(quarantined)),
        )  # fmt: skip


class D1Universe:
    """A committed manifest as a `ScreenUniverse`: the names to screen and the days to skip."""

    def __init__(self, manifest: D1Manifest) -> None:
        self._manifest = manifest
        self._quarantined = frozenset(manifest.quarantined)

    @classmethod
    def load(cls, path: Path = DEFAULT_MANIFEST) -> D1Universe:
        return cls(D1Manifest.load(path))

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return self._manifest.included

    @property
    def universe_label(self) -> str:
        return f"d1-{len(self._manifest.included)}-{self._manifest.content_hash[:8]}"

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) in self._quarantined

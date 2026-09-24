"""The one-shot vault (EM-191 §4.3, EDGE_SEARCH_PLAN.md §3.6): a slice of history nothing may read
until a frozen candidate is put to it, at most three times in the whole program.

The point of a holdout is that it is looked at once. Every glance at it while choosing what to try
next turns it into training data, and the loop this plan runs would glance constantly. So the seal
is structural: `VaultedCandleReader` sits between analysis and the candle source and REFUSES any
read that touches the sealed range, whoever asks, unless a committed `UnsealRecord` for that exact
candidate admits it. There is no flag that turns it off.

Two pieces of state, both plain data checked into git (`docs/research/edge-search/`):

* `VaultSeal` — what is sealed: the day range, and the instruments (`None` = every instrument, the
  time-only vault the plan allows until D1 widens the universe).
* `UnsealRecord` — one open: its number, the candidate's hash, why, and the range it admits.

`VaultGate` validates them together (numbers 1..max_opens, no gaps or repeats, each record inside
the vault and bound to THIS seal), so the "at most 3 opens" budget cannot be exceeded by adding a
fourth file: the gate will not even build.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from emporos.core.clock import IST
from emporos.core.errors import DefinitiveError
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candles import CandleReader

__all__ = [
    "UnsealRecord", "VaultBurnedError", "VaultGate", "VaultSeal", "VaultViolation",
    "VaultedCandleReader",
]  # fmt: skip

MAX_OPENS = 3  # plan §3.6: the whole program


class VaultViolation(DefinitiveError):
    """A read touched the sealed range without a committed unseal record for it."""


class VaultBurnedError(DefinitiveError):
    """The seal or its unseal records are inconsistent, or every open has been used."""


def _interval(first: date, last: date) -> tuple[datetime, datetime]:
    """Inclusive IST calendar days as a half-open range of instants."""
    start = datetime.combine(first, time(0, 0), tzinfo=IST)
    end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
    return start, end


@dataclass(frozen=True)
class VaultSeal:
    first_day: date
    last_day: date
    instrument_ids: frozenset[str] | None  # None: every instrument (time-only, until D1)
    max_opens: int = MAX_OPENS

    def __post_init__(self) -> None:
        if self.first_day > self.last_day:
            raise ValueError("the vault's first day is after its last")
        if not 1 <= self.max_opens <= MAX_OPENS:
            raise ValueError(f"the vault allows 1 to {MAX_OPENS} opens, not {self.max_opens}")
        if self.instrument_ids is not None and not self.instrument_ids:
            raise ValueError("a sealed instrument set cannot be empty (use None for every one)")

    @property
    def content_hash(self) -> str:
        """Identifies THIS seal: an unseal record names it, so a re-sealed vault (D1 adds its
        instrument set) invalidates records made against the old one instead of inheriting them."""
        canonical = json.dumps(
            {
                "first_day": self.first_day.isoformat(),
                "last_day": self.last_day.isoformat(),
                "instruments": None if self.instrument_ids is None else sorted(self.instrument_ids),
                "max_opens": self.max_opens,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def seals(self, instrument_id: str) -> bool:
        return self.instrument_ids is None or instrument_id in self.instrument_ids

    def window(self) -> tuple[datetime, datetime]:
        return _interval(self.first_day, self.last_day)


@dataclass(frozen=True)
class UnsealRecord:
    open_number: int
    seal_hash: str
    candidate_hash: str  # the frozen config hash + code commit of the one candidate put to it
    reason: str
    opened_at: datetime
    first_day: date
    last_day: date
    instrument_ids: frozenset[str] | None  # the candidate's universe; None: every sealed one

    def __post_init__(self) -> None:
        if self.open_number < 1:
            raise ValueError("an open is numbered from 1")
        if not self.candidate_hash.strip() or not self.reason.strip():
            raise ValueError("an unseal record names its candidate and says why")
        if self.opened_at.tzinfo is None:
            raise ValueError("an unseal record's time must be timezone-aware")
        if self.first_day > self.last_day:
            raise ValueError("an unseal record's first day is after its last")

    def admits(self, instrument_id: str, start: datetime, end: datetime) -> bool:
        """True when it covers `instrument_id` over the whole of [start, end)."""
        if self.instrument_ids is not None and instrument_id not in self.instrument_ids:
            return False
        first, last = _interval(self.first_day, self.last_day)
        return first <= start and end <= last


class VaultGate:
    def __init__(self, seal: VaultSeal, opens: Sequence[UnsealRecord] = ()) -> None:
        self._seal = seal
        self._opens = tuple(sorted(opens, key=lambda o: o.open_number))
        self._validate()

    def __eq__(self, other: object) -> bool:
        """Two gates are the same gate when they hold the same seal and the same opens: a worker's
        copy, sent across a process boundary, must compare equal to the original."""
        return isinstance(other, VaultGate) and (self._seal, self._opens) == (
            other._seal,
            other._opens,
        )

    def __hash__(self) -> int:
        return hash((self._seal, self._opens))

    @property
    def seal(self) -> VaultSeal:
        return self._seal

    @property
    def opens_used(self) -> int:
        return len(self._opens)

    @property
    def opens_left(self) -> int:
        return self._seal.max_opens - len(self._opens)

    def _validate(self) -> None:
        if len(self._opens) > self._seal.max_opens:
            raise VaultBurnedError(
                f"{len(self._opens)} unseal records but the vault allows {self._seal.max_opens}: "
                "it is burned, and only forward paper trading is clean evidence now"
            )
        numbers = [o.open_number for o in self._opens]
        if numbers != list(range(1, len(numbers) + 1)):
            raise VaultBurnedError(
                f"unseal records are numbered {numbers}; they must be 1..{len(numbers)} with "
                "no gap and no repeat"
            )
        first, last = self._seal.window()
        for record in self._opens:
            if record.seal_hash != self._seal.content_hash:
                raise VaultBurnedError(
                    f"open {record.open_number} was made against a different seal "
                    f"({record.seal_hash[:12]}), not {self._seal.content_hash[:12]}"
                )
            opened_first, opened_last = _interval(record.first_day, record.last_day)
            if opened_first < first or opened_last > last:
                raise VaultBurnedError(
                    f"open {record.open_number} admits days outside the vault "
                    f"{self._seal.first_day}..{self._seal.last_day}"
                )

    def check(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        *,
        candidate_hash: str | None = None,
    ) -> None:
        """Raise `VaultViolation` when [start, end) touches the sealed range for this instrument
        and no unseal record for `candidate_hash` admits the whole read."""
        if not self._seal.seals(instrument_id):
            return
        vault_start, vault_end = self._seal.window()
        if end <= vault_start or start >= vault_end:
            return
        # only the part of the read inside the vault needs admitting: warm-up bars from before the
        # sealed days are ordinary history
        touched = (max(start, vault_start), min(end, vault_end))
        if candidate_hash is not None and any(
            o.candidate_hash == candidate_hash and o.admits(instrument_id, *touched)
            for o in self._opens
        ):
            return
        raise VaultViolation(self._refusal(instrument_id, start, end, candidate_hash))

    def _refusal(
        self, instrument_id: str, start: datetime, end: datetime, candidate_hash: str | None
    ) -> str:
        seal = self._seal
        first, last = start.astimezone(IST), end.astimezone(IST)
        base = (
            f"{instrument_id} {first:%Y-%m-%d %H:%M}..{last:%Y-%m-%d %H:%M}"
            f" touches the vault ({seal.first_day}..{seal.last_day}), which only the S5 stage may "
            "read, once per frozen candidate."
        )
        if candidate_hash is None:
            return f"{base} No candidate was named."
        if self.opens_left == 0:
            return f"{base} All {seal.max_opens} opens are used: the vault is burned."
        return f"{base} No committed unseal record admits this candidate over this range."


class VaultedCandleReader:
    """A `CandleReader` that cannot see the vault. Wrap every analysis reader in one."""

    def __init__(
        self, inner: CandleReader, gate: VaultGate, candidate_hash: str | None = None
    ) -> None:
        self._inner = inner
        self._gate = gate
        self._candidate = candidate_hash

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self._gate.check(instrument_id, start, end, candidate_hash=self._candidate)
        return await self._inner.get_range(instrument_id, timeframe, start, end)

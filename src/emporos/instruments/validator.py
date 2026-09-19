"""The instrument-master validation gate (plan.md §8) — the critical safeguard.

A truncated or malformed upstream file must never replace the current master.
Validation rejects the whole file (raising `MasterRejectedError`) when:

* it has too few rows, or a row count outside ±20% of the current master's;
* too many individual rows are unusable (missing fields, non-positive lot/tick size);
* the same (exchange, token) appears twice.

A small fraction of bad rows is tolerated and dropped, because the real file
legitimately carries a handful of index-like cash rows with a zero tick size.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.instruments.downloader import DownloadedMaster
from emporos.instruments.errors import MasterRejectedError

REQUIRED_FIELDS = ("token", "symbol", "name", "exch_seg", "lotsize", "tick_size")
_PAISE_PER_RUPEE = Decimal(100)  # Angel One publishes tick_size in paise


@dataclass(frozen=True)
class ValidationPolicy:
    row_count_tolerance: Decimal = Decimal("0.20")
    max_invalid_fraction: Decimal = Decimal("0.01")
    min_rows: int = 1000


@dataclass(frozen=True)
class ValidatedMaster:
    instruments: tuple[Instrument, ...]
    dropped_rows: int


class RowParser:
    """Raw upstream row → `Instrument`, or a `ValueError` naming what is wrong."""

    def parse(self, row: Mapping[str, Any]) -> Instrument:
        missing = [name for name in REQUIRED_FIELDS if row.get(name) in (None, "")]
        if missing:
            raise ValueError(f"missing field {missing[0]}")
        try:
            exchange = Exchange(str(row["exch_seg"]))
            lot_size = int(str(row["lotsize"]))
            tick_size = Money(Decimal(str(row["tick_size"])) / _PAISE_PER_RUPEE)
        except (ValueError, InvalidOperation, ArithmeticError) as error:
            raise ValueError("unparseable value") from error
        try:
            return Instrument(
                exchange=exchange,
                token=str(row["token"]),
                tradingsymbol=str(row["symbol"]),
                name=str(row["name"]),
                lot_size=lot_size,
                tick_size=Money(tick_size.amount.normalize()),
            )
        except ValueError as error:
            raise ValueError(str(error)) from error


class InstrumentMasterValidator:
    def __init__(
        self, policy: ValidationPolicy | None = None, parser: RowParser | None = None
    ) -> None:
        self._policy = policy or ValidationPolicy()
        self._parser = parser or RowParser()

    def validate(self, master: DownloadedMaster, current_count: int) -> ValidatedMaster:
        """Return the usable instruments, or raise `MasterRejectedError` listing every reason."""
        reasons: list[str] = []
        total = len(master.rows)
        reasons.extend(self._row_count_reasons(total, current_count))

        instruments: list[Instrument] = []
        invalid: Counter[str] = Counter()
        for row in master.rows:
            try:
                instruments.append(self._parser.parse(row))
            except ValueError as error:
                invalid[str(error)] += 1
        reasons.extend(self._invalid_reasons(total, invalid))
        reasons.extend(self._duplicate_reasons(instruments))

        if reasons:
            raise MasterRejectedError(reasons)
        return ValidatedMaster(tuple(instruments), dropped_rows=sum(invalid.values()))

    def _row_count_reasons(self, total: int, current_count: int) -> list[str]:
        reasons: list[str] = []
        if total < self._policy.min_rows:
            reasons.append(f"only {total} cash rows (minimum {self._policy.min_rows})")
        if current_count > 0:
            drift = abs(Decimal(total - current_count)) / Decimal(current_count)
            if drift > self._policy.row_count_tolerance:
                reasons.append(
                    f"row count {total} is {drift:.0%} away from the current {current_count} "
                    f"(tolerance {self._policy.row_count_tolerance:.0%})"
                )
        return reasons

    def _invalid_reasons(self, total: int, invalid: Counter[str]) -> list[str]:
        bad = sum(invalid.values())
        if total == 0 or Decimal(bad) / Decimal(total) <= self._policy.max_invalid_fraction:
            return []
        breakdown = ", ".join(f"{count} x {reason}" for reason, count in invalid.most_common(3))
        return [f"{bad} of {total} rows are unusable ({breakdown})"]

    @staticmethod
    def _duplicate_reasons(instruments: list[Instrument]) -> list[str]:
        counts = Counter(instrument.instrument_id for instrument in instruments)
        duplicated = sorted(key for key, count in counts.items() if count > 1)
        if not duplicated:
            return []
        return [f"{len(duplicated)} duplicated (exchange, token) keys, e.g. {duplicated[0]}"]

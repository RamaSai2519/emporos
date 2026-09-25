"""The lot size and the expiry calendar in force on each date, per index (EM-225, PROFIT_PLAN §5).

Both changed over the years (lot sizes repeatedly, weekly expiries added and removed), so a
backtest must not assume today's. The expiry calendar is what the exchange LISTED that day: the
distinct option and future expiries in that day's file. The lot size is exact where the file
carries it (the UDiFF layout's `NewBrdLotQty`). Where it does not (the legacy layout), it is
INFERRED from index futures: turnover = lots traded x lot size x price, so lot size = turnover /
(contracts x close), taken as the median over the day's futures that traded and rounded to a whole
number. An inferred lot size is marked as such, with how many futures it rests on, and is checked
against the exact one wherever both exist (`InferenceCheck`). A day with no trading futures has no
inferred lot size."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind

__all__ = [
    "ContractSpec",
    "ContractSpecBuilder",
    "InferenceCheck",
    "LotSource",
    "read_specs",
    "write_specs",
]

_MIN_LOTS_TRADED = 10  # fewer lots than this and the turnover's rounding swamps the price


def _snap(estimate: Decimal) -> int:
    """The nearest multiple of 5. Turnover is at the day's average trade price, not the close, so
    the estimate is off by a percent or two (76 for a lot of 75); every index lot size in the
    archive is a multiple of 5, and a percent is far under half of the smallest gap."""
    return int((estimate / 5).to_integral_value()) * 5


class LotSource(StrEnum):
    EXCHANGE = "exchange"  # carried in the file
    INFERRED = "inferred"  # worked out from futures turnover
    UNKNOWN = "unknown"  # neither: no file value and no trading future to infer from


@dataclass(frozen=True)
class ContractSpec:
    day: date
    symbol: str
    lot_size: int | None  # the nearest expiry's
    lot_source: LotSource
    evidence: int  # futures rows the inferred lot size rests on (0 for exchange or unknown)
    option_expiries: tuple[date, ...]
    future_expiries: tuple[date, ...]
    # the lot size of each expiry that has one known: an exchange changes a lot size for NEW
    # expiries only, so two expiries listed on one day can differ (FINNIFTY on 2024-07-08)
    expiry_lots: tuple[tuple[date, int], ...] = ()

    def lot_for(self, expiry: date) -> int | None:
        return dict(self.expiry_lots).get(expiry, self.lot_size)


@dataclass(frozen=True)
class InferenceCheck:
    """Where an exchange lot size and an inferred one exist for the same day and symbol."""

    day: date
    symbol: str
    exchange: int
    inferred: int

    @property
    def agrees(self) -> bool:
        return self.exchange == self.inferred


class ContractSpecBuilder:
    def build(self, rows: Iterable[IndexContractRow]) -> list[ContractSpec]:
        """One spec per (day, symbol) in the rows, oldest day first."""
        by_key: dict[tuple[date, str], list[IndexContractRow]] = defaultdict(list)
        for row in rows:
            by_key[(row.day, row.symbol)].append(row)
        return [self._spec(day, symbol, group) for (day, symbol), group in sorted(by_key.items())]

    def check(self, rows: Iterable[IndexContractRow]) -> list[InferenceCheck]:
        """Compare the inference with the exchange's own lot size, per expiry, wherever the file
        carries both (only futures can be inferred, so the exchange value is read from the same
        futures rows)."""
        by_key: dict[tuple[date, str], list[IndexContractRow]] = defaultdict(list)
        for row in rows:
            by_key[(row.day, row.symbol)].append(row)
        out: list[InferenceCheck] = []
        for (day, symbol), group in sorted(by_key.items()):
            exchange = self._exchange_lots(day, symbol, group)
            inferred = self._inferred_lots(group)
            for expiry in sorted(exchange.keys() & inferred.keys()):
                out.append(InferenceCheck(day, symbol, exchange[expiry], inferred[expiry][0]))
        return out

    def _spec(self, day: date, symbol: str, group: Sequence[IndexContractRow]) -> ContractSpec:
        exchange = self._exchange_lots(day, symbol, group)
        inferred = self._inferred_lots(group)
        options = sorted({r.expiry for r in group if r.kind is InstrumentKind.OPTION})
        futures = sorted({r.expiry for r in group if r.kind is InstrumentKind.FUTURE})
        lots = {e: (lot, LotSource.INFERRED, n) for e, (lot, n) in inferred.items()}
        lots.update({e: (lot, LotSource.EXCHANGE, 0) for e, lot in exchange.items()})
        if not lots:
            return ContractSpec(
                day, symbol, None, LotSource.UNKNOWN, 0, tuple(options), tuple(futures)
            )
        nearest = min(lots)
        lot, source, evidence = lots[nearest]
        expiry_lots = tuple(sorted((e, v[0]) for e, v in lots.items()))
        return ContractSpec(
            day, symbol, lot, source, evidence, tuple(options), tuple(futures), expiry_lots
        )

    @staticmethod
    def _exchange_lots(
        day: date, symbol: str, group: Sequence[IndexContractRow]
    ) -> dict[date, int]:
        found: dict[date, set[int]] = defaultdict(set)
        for r in group:
            if r.lot_size is not None:
                found[r.expiry].add(r.lot_size)
        for expiry, lots in found.items():
            if len(lots) > 1:
                raise ValueError(f"{symbol} on {day}: two lot sizes for {expiry}: {sorted(lots)}")
        return {e: next(iter(v)) for e, v in found.items()}

    @staticmethod
    def _inferred_lots(group: Sequence[IndexContractRow]) -> dict[date, tuple[int, int]]:
        """Per FUTURE expiry, (lot size, rows it rests on), from turnover / (lots x close)."""
        by_expiry: dict[date, list[Decimal]] = defaultdict(list)
        for r in group:
            if r.kind is InstrumentKind.FUTURE and r.contracts >= _MIN_LOTS_TRADED and r.close > 0:
                by_expiry[r.expiry].append(r.turnover / (Decimal(r.contracts) * r.close))
        return {e: (_snap(statistics.median(v)), len(v)) for e, v in by_expiry.items()}


_SPEC_SCHEMA = pa.schema(
    [
        ("day", pa.date32()), ("symbol", pa.string()), ("lot_size", pa.int32()),
        ("lot_source", pa.string()), ("evidence", pa.int32()),
        ("option_expiries", pa.list_(pa.date32())), ("future_expiries", pa.list_(pa.date32())),
        ("lot_expiries", pa.list_(pa.date32())), ("lot_values", pa.list_(pa.int32())),
    ]
)  # fmt: skip


def write_specs(path: Path, specs: Sequence[ContractSpec]) -> None:
    table = pa.Table.from_pydict(
        {
            "day": [s.day for s in specs],
            "symbol": [s.symbol for s in specs],
            "lot_size": [s.lot_size for s in specs],
            "lot_source": [s.lot_source.value for s in specs],
            "evidence": [s.evidence for s in specs],
            "option_expiries": [list(s.option_expiries) for s in specs],
            "future_expiries": [list(s.future_expiries) for s in specs],
            "lot_expiries": [[e for e, _ in s.expiry_lots] for s in specs],
            "lot_values": [[v for _, v in s.expiry_lots] for s in specs],
        },
        schema=_SPEC_SCHEMA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".parquet.tmp")
    pq.write_table(table, staging, compression="zstd")
    staging.replace(path)


def read_specs(path: Path) -> list[ContractSpec]:
    return [
        ContractSpec(
            r["day"],
            r["symbol"],
            r["lot_size"],
            LotSource(r["lot_source"]),
            r["evidence"],
            tuple(r["option_expiries"]),
            tuple(r["future_expiries"]),
            tuple(zip(r["lot_expiries"], r["lot_values"], strict=True)),
        )
        for r in pq.read_table(path, schema=_SPEC_SCHEMA).to_pylist()
    ]

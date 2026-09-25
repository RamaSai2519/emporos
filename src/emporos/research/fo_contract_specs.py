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


class LotSource(StrEnum):
    EXCHANGE = "exchange"  # carried in the file
    INFERRED = "inferred"  # worked out from futures turnover
    UNKNOWN = "unknown"  # neither: no file value and no trading future to infer from


@dataclass(frozen=True)
class ContractSpec:
    day: date
    symbol: str
    lot_size: int | None
    lot_source: LotSource
    evidence: int  # futures rows the inferred lot size rests on (0 for exchange or unknown)
    option_expiries: tuple[date, ...]
    future_expiries: tuple[date, ...]


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
        """Compare the inference with the exchange's own lot size wherever the file carries both
        (only futures can be inferred, so the exchange value is read from the same rows)."""
        by_key: dict[tuple[date, str], list[IndexContractRow]] = defaultdict(list)
        for row in rows:
            by_key[(row.day, row.symbol)].append(row)
        out: list[InferenceCheck] = []
        for (day, symbol), group in sorted(by_key.items()):
            exchange = {r.lot_size for r in group if r.lot_size is not None}
            inferred = self._infer(group)
            if len(exchange) == 1 and inferred is not None:
                out.append(InferenceCheck(day, symbol, exchange.pop(), inferred[0]))
        return out

    def _spec(self, day: date, symbol: str, group: Sequence[IndexContractRow]) -> ContractSpec:
        exchange = {r.lot_size for r in group if r.lot_size is not None}
        if len(exchange) > 1:
            raise ValueError(f"{symbol} on {day}: two lot sizes in one file: {sorted(exchange)}")
        lot: int | None
        if exchange:
            lot, source, evidence = exchange.pop(), LotSource.EXCHANGE, 0
        elif (inferred := self._infer(group)) is not None:
            (lot, evidence), source = inferred, LotSource.INFERRED
        else:
            lot, source, evidence = None, LotSource.UNKNOWN, 0
        options = sorted({r.expiry for r in group if r.kind is InstrumentKind.OPTION})
        futures = sorted({r.expiry for r in group if r.kind is InstrumentKind.FUTURE})
        return ContractSpec(day, symbol, lot, source, evidence, tuple(options), tuple(futures))

    @staticmethod
    def _infer(group: Sequence[IndexContractRow]) -> tuple[int, int] | None:
        """(lot size, futures rows it rests on), or none if no future traded enough."""
        estimates = [
            r.turnover / (Decimal(r.contracts) * r.close)
            for r in group
            if r.kind is InstrumentKind.FUTURE and r.contracts >= _MIN_LOTS_TRADED and r.close > 0
        ]
        if not estimates:
            return None
        return int(statistics.median(estimates).to_integral_value()), len(estimates)


_SPEC_SCHEMA = pa.schema(
    [
        ("day", pa.date32()), ("symbol", pa.string()), ("lot_size", pa.int32()),
        ("lot_source", pa.string()), ("evidence", pa.int32()),
        ("option_expiries", pa.list_(pa.date32())), ("future_expiries", pa.list_(pa.date32())),
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
        )  # fmt: skip
        for r in pq.read_table(path, schema=_SPEC_SCHEMA).to_pylist()
    ]

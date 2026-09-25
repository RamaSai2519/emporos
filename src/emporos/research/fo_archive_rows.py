"""One day's NSE index-derivative contracts, read from the exchange's public F&O bhavcopy (EM-225,
PROFIT_PLAN.md §5 B-F1).

The archive has two layouts. The legacy file (`fo<DD><MON><YYYY>bhav.csv`) is named by columns like
INSTRUMENT and SYMBOL and holds no lot size and no underlying level. The UDiFF file that replaced it
carries `NewBrdLotQty` (the lot size) and `UndrlygPric` (the index level). Both are read into the
same
`IndexContractRow`; a field a layout does not carry stays `None`, never a guess. Only INDEX futures
and options are kept (stock derivatives are the archive's bulk and no part of this program).

What the file does not hold, and nothing here can supply: intraday prices, bid-ask, implied
volatility. A contract that did not trade still gets a row whose `close` is carried from an earlier
day, so `contracts` must be read before `close` is trusted."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Protocol

from emporos.options.chain import OptionRight

__all__ = [
    "ArchiveFormat",
    "ArchiveParseError",
    "IndexContractRow",
    "InstrumentKind",
    "LegacyParser",
    "UdiffParser",
    "parser_for",
    "read_archive",
]

_LAKH = Decimal(100_000)


class ArchiveParseError(ValueError):
    """The file is not a bhavcopy this reader understands. Refused, never guessed at."""


class ArchiveFormat(StrEnum):
    LEGACY = "legacy"
    UDIFF = "udiff"


class InstrumentKind(StrEnum):
    FUTURE = "FUT"
    OPTION = "OPT"


@dataclass(frozen=True)
class IndexContractRow:
    day: date
    symbol: str
    kind: InstrumentKind
    expiry: date
    strike: Decimal | None
    right: OptionRight | None
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    settle: Decimal
    contracts: int  # lots traded
    turnover: Decimal  # rupees (options: notional, as the exchange reports it)
    open_interest: int  # units
    change_in_oi: int
    underlying: Decimal | None  # UDiFF only
    lot_size: int | None  # UDiFF only
    source: ArchiveFormat

    def __post_init__(self) -> None:
        if (self.kind is InstrumentKind.OPTION) != (self.right is not None):
            raise ValueError("an option has a right and a future has none")
        if (self.kind is InstrumentKind.OPTION) != (self.strike is not None):
            raise ValueError("an option has a strike and a future has none")


class ArchiveParser(Protocol):
    format: ArchiveFormat

    def parse(self, day: date, text: str) -> list[IndexContractRow]: ...


def _decimal(row: Mapping[str, str], key: str) -> Decimal:
    try:
        return Decimal(row[key].strip())
    except (KeyError, ArithmeticError, ValueError) as error:
        raise ArchiveParseError(f"{key}: not a number in {dict(row)}") from error


def _whole(row: Mapping[str, str], key: str) -> int:
    value = _decimal(row, key)
    if value != value.to_integral_value():
        raise ArchiveParseError(f"{key}: {value} is not a whole number")
    return int(value)


def _require(header: Sequence[str] | None, needed: frozenset[str], what: str) -> None:
    missing = needed - set(header or [])
    if missing:
        raise ArchiveParseError(f"{what}: columns missing: {sorted(missing)}")


class UdiffParser:
    """`BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv`: index futures `IDF`, index options `IDO`."""

    format = ArchiveFormat.UDIFF
    _KINDS: ClassVar[Mapping[str, InstrumentKind]] = {
        "IDF": InstrumentKind.FUTURE,
        "IDO": InstrumentKind.OPTION,
    }
    _COLUMNS: ClassVar[frozenset[str]] = frozenset({
        "TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "OpnPric", "HghPric",
        "LwPric", "ClsPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol",
        "TtlTrfVal", "UndrlygPric", "NewBrdLotQty",
    })  # fmt: skip

    def parse(self, day: date, text: str) -> list[IndexContractRow]:
        reader = csv.DictReader(io.StringIO(text))
        _require(reader.fieldnames, self._COLUMNS, "UDiFF bhavcopy")
        rows: list[IndexContractRow] = []
        for raw in reader:
            kind = self._KINDS.get(raw["FinInstrmTp"])
            if kind is None:
                continue
            if date.fromisoformat(raw["TradDt"]) != day:
                raise ArchiveParseError(f"{raw['TradDt']} in the file of {day.isoformat()}")
            rows.append(self._row(day, kind, raw))
        return rows

    @staticmethod
    def _row(day: date, kind: InstrumentKind, raw: Mapping[str, str]) -> IndexContractRow:
        option = kind is InstrumentKind.OPTION
        return IndexContractRow(
            day=day,
            symbol=raw["TckrSymb"],
            kind=kind,
            expiry=date.fromisoformat(raw["XpryDt"]),
            strike=_decimal(raw, "StrkPric") if option else None,
            right=OptionRight(raw["OptnTp"]) if option else None,
            open=_decimal(raw, "OpnPric"),
            high=_decimal(raw, "HghPric"),
            low=_decimal(raw, "LwPric"),
            close=_decimal(raw, "ClsPric"),
            settle=_decimal(raw, "SttlmPric"),
            contracts=_whole(raw, "TtlTradgVol"),
            turnover=_decimal(raw, "TtlTrfVal"),
            open_interest=_whole(raw, "OpnIntrst"),
            change_in_oi=_whole(raw, "ChngInOpnIntrst"),
            underlying=_decimal(raw, "UndrlygPric"),
            lot_size=_whole(raw, "NewBrdLotQty"),
            source=ArchiveFormat.UDIFF,
        )


class LegacyParser:
    """`fo<DD><MON><YYYY>bhav.csv`: `FUTIDX` and `OPTIDX` rows (option type `XX` marks a future)."""

    format = ArchiveFormat.LEGACY
    _type_column = "OPTION_TYP"
    _KINDS: ClassVar[Mapping[str, InstrumentKind]] = {
        "FUTIDX": InstrumentKind.FUTURE,
        "OPTIDX": InstrumentKind.OPTION,
    }
    _COLUMNS: ClassVar[frozenset[str]] = frozenset({
        "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPEN", "HIGH", "LOW",
        "CLOSE", "SETTLE_PR", "CONTRACTS", "VAL_INLAKH", "OPEN_INT", "CHG_IN_OI", "TIMESTAMP",
    })  # fmt: skip

    def parse(self, day: date, text: str) -> list[IndexContractRow]:
        reader = csv.DictReader(io.StringIO(text))
        _require(reader.fieldnames, self._COLUMNS, "legacy bhavcopy")
        # the option-type column was called OPTIONTYPE in the oldest files
        self._type_column = (
            "OPTION_TYP" if "OPTION_TYP" in (reader.fieldnames or ()) else "OPTIONTYPE"
        )
        _require(reader.fieldnames, frozenset({self._type_column}), "legacy bhavcopy")
        rows: list[IndexContractRow] = []
        for raw in reader:
            kind = self._KINDS.get(raw["INSTRUMENT"])
            if kind is None:
                continue
            if self._date(raw["TIMESTAMP"]) != day:
                raise ArchiveParseError(f"{raw['TIMESTAMP']} in the file of {day.isoformat()}")
            rows.append(self._row(day, kind, raw))
        return rows

    @staticmethod
    def _date(text: str) -> date:
        try:
            return datetime.strptime(text.strip().title(), "%d-%b-%Y").date()
        except ValueError as error:
            raise ArchiveParseError(f"not a date: {text!r}") from error

    def _row(self, day: date, kind: InstrumentKind, raw: Mapping[str, str]) -> IndexContractRow:
        option = kind is InstrumentKind.OPTION
        return IndexContractRow(
            day=day,
            symbol=raw["SYMBOL"],
            kind=kind,
            expiry=self._date(raw["EXPIRY_DT"]),
            strike=_decimal(raw, "STRIKE_PR") if option else None,
            right=OptionRight(raw[self._type_column]) if option else None,
            open=_decimal(raw, "OPEN"),
            high=_decimal(raw, "HIGH"),
            low=_decimal(raw, "LOW"),
            close=_decimal(raw, "CLOSE"),
            settle=_decimal(raw, "SETTLE_PR"),
            contracts=_whole(raw, "CONTRACTS"),
            turnover=_decimal(raw, "VAL_INLAKH") * _LAKH,
            open_interest=_whole(raw, "OPEN_INT"),
            change_in_oi=_whole(raw, "CHG_IN_OI"),
            underlying=None,
            lot_size=None,
            source=ArchiveFormat.LEGACY,
        )


_PARSERS: Mapping[ArchiveFormat, Callable[[], ArchiveParser]] = {
    ArchiveFormat.LEGACY: LegacyParser,
    ArchiveFormat.UDIFF: UdiffParser,
}


def parser_for(fmt: ArchiveFormat) -> ArchiveParser:
    return _PARSERS[fmt]()


def read_archive(day: date, payload: bytes, fmt: ArchiveFormat) -> list[IndexContractRow]:
    """The index contracts in one downloaded zip. The zip must hold exactly one CSV."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ArchiveParseError(f"expected one CSV in the zip, found {names}")
            text = archive.read(names[0]).decode("utf-8-sig")
    except zipfile.BadZipFile as error:
        raise ArchiveParseError("the download is not a zip file") from error
    return parser_for(fmt).parse(day, text)

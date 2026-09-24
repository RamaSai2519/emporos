"""The declared reference series, and the check that the broker's master still agrees (EM-191 D2).

`ReferenceSeriesCatalog` reads `config/reference_series.yaml` strictly. `ReferenceSeriesVerifier`
compares it with the scrip master's raw rows: an index token that moved or was renamed would
otherwise fetch some other series under this one's name, silently. The raw rows are used here and
nowhere else: the instrument master drops index rows, and that stays true.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError, DefinitiveError
from emporos.domain.reference_series import ReferenceSeries, SeriesKind

__all__ = [
    "DEFAULT_CATALOG_FILE", "ReferenceSeriesCatalog", "ReferenceSeriesMismatch",
    "ReferenceSeriesVerifier",
]  # fmt: skip

DEFAULT_CATALOG_FILE = CONFIG_DIR / "reference_series.yaml"
_INDEX_TYPE = "AMXIDX"
_KEYS = {"token", "symbol", "name", "kind"}


class ReferenceSeriesMismatch(DefinitiveError):
    """The broker's master no longer describes a declared series as the catalog does."""


class ReferenceSeriesCatalog:
    def __init__(self, series: Sequence[ReferenceSeries]) -> None:
        tokens = [s.token for s in series]
        if len(set(tokens)) != len(tokens):
            raise ConfigurationError("a reference series token is declared twice")
        symbols = [s.symbol for s in series]
        if len(set(symbols)) != len(symbols):
            raise ConfigurationError("a reference series symbol is declared twice")
        self._series = tuple(series)

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_FILE) -> ReferenceSeriesCatalog:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get("series"), list):
            raise ConfigurationError(f"{path} must have a `series` list")
        return cls([cls._entry(path, entry) for entry in document["series"]])

    @staticmethod
    def _entry(path: Path, entry: object) -> ReferenceSeries:
        if not isinstance(entry, dict) or set(entry) != _KEYS:
            raise ConfigurationError(f"{path}: each series needs exactly {sorted(_KEYS)}")
        try:
            return ReferenceSeries(
                str(entry["token"]), str(entry["symbol"]), str(entry["name"]),
                SeriesKind(str(entry["kind"])),
            )  # fmt: skip
        except ValueError as error:
            raise ConfigurationError(f"{path}: {error}") from error

    def all(self) -> tuple[ReferenceSeries, ...]:
        return self._series

    def select(self, symbols: Sequence[str]) -> tuple[ReferenceSeries, ...]:
        """The named series (all of them when none are named); an unknown name is an error."""
        if not symbols:
            return self._series
        by_symbol = {s.symbol: s for s in self._series}
        unknown = [name for name in symbols if name not in by_symbol]
        if unknown:
            raise ConfigurationError(
                f"unknown reference series: {', '.join(unknown)} "
                f"(declared: {', '.join(sorted(by_symbol))})"
            )
        return tuple(by_symbol[name] for name in symbols)


class ReferenceSeriesVerifier:
    def verify(
        self, series: Iterable[ReferenceSeries], master_rows: Iterable[Mapping[str, Any]]
    ) -> None:
        """Raise `ReferenceSeriesMismatch` unless every series is an NSE index row in the master
        with the declared token, symbol and name."""
        index_rows = {
            str(row.get("token")): row
            for row in master_rows
            if row.get("exch_seg") == "NSE" and row.get("instrumenttype") == _INDEX_TYPE
        }
        problems: list[str] = []
        for s in series:
            row = index_rows.get(s.token)
            if row is None:
                problems.append(f"{s.symbol} ({s.token}): no NSE index row with that token")
            elif (row.get("symbol"), row.get("name")) != (s.symbol, s.name):
                problems.append(
                    f"{s.symbol} ({s.token}): the master calls it "
                    f"{row.get('symbol')!r}/{row.get('name')!r}, not {s.symbol!r}/{s.name!r}"
                )
        if problems:
            raise ReferenceSeriesMismatch("; ".join(problems))

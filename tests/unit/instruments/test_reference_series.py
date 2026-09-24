"""EM-191 D2: reference series are declared, verified against the broker's master, and cannot be
mistaken for tradable instruments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emporos.core.errors import ConfigurationError
from emporos.domain.instruments import Exchange
from emporos.domain.reference_series import ReferenceSeries, SeriesKind
from emporos.instruments.downloader import CashSegmentFilter
from emporos.instruments.reference_series import (
    DEFAULT_CATALOG_FILE,
    ReferenceSeriesCatalog,
    ReferenceSeriesMismatch,
    ReferenceSeriesVerifier,
)

FIXTURE = Path("tests/fixtures/reference_series_master_rows.json")


def master_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(FIXTURE.read_text())
    return rows


NIFTY = ReferenceSeries("99926000", "Nifty 50", "NIFTY", SeriesKind.INDEX)


class TestSeries:
    def test_its_id_is_the_exchange_and_token_and_its_fetch_handle_agrees(self) -> None:
        handle = NIFTY.fetch_handle()

        assert NIFTY.series_id == "NSE:99926000" == handle.instrument_id
        assert (handle.exchange, handle.token, handle.lot_size) == (Exchange.NSE, "99926000", 1)

    @pytest.mark.parametrize("field", ["token", "symbol", "name"])
    def test_a_series_needs_its_identity(self, field: str) -> None:
        values = {"token": "1", "symbol": "s", "name": "n", field: " "}

        with pytest.raises(ValueError, match="needs a token"):
            ReferenceSeries(kind=SeriesKind.INDEX, **values)


class TestShippedCatalog:
    def test_it_declares_the_plans_series(self) -> None:
        catalog = ReferenceSeriesCatalog.load()
        symbols = {s.symbol for s in catalog.all()}

        assert {"Nifty 50", "Nifty Bank", "India VIX"} <= symbols
        assert len(catalog.all()) == 15
        assert [s.symbol for s in catalog.all() if s.kind is SeriesKind.VOLATILITY] == ["India VIX"]

    def test_every_declared_series_matches_the_recorded_master(self) -> None:
        ReferenceSeriesVerifier().verify(ReferenceSeriesCatalog.load().all(), master_rows())

    def test_none_of_them_can_become_a_tradable_instrument(self) -> None:
        """The instrument master keeps only cash rows; an index row is dropped by its filter, so a
        reference series never reaches anything that could size or order it."""
        accepts = CashSegmentFilter().accepts

        assert master_rows() and not any(accepts(row) for row in master_rows())


class TestCatalogLoading:
    def write(self, tmp_path: Path, text: str) -> Path:
        path = tmp_path / "series.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_duplicate_token_or_symbol_is_refused(self) -> None:
        other = ReferenceSeries("99926000", "Other", "OTHER", SeriesKind.INDEX)
        renamed = ReferenceSeries("1", "Nifty 50", "X", SeriesKind.INDEX)

        with pytest.raises(ConfigurationError, match="token"):
            ReferenceSeriesCatalog([NIFTY, other])
        with pytest.raises(ConfigurationError, match="symbol"):
            ReferenceSeriesCatalog([NIFTY, renamed])

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("- a list", "`series` list"),
            ("series: 3", "`series` list"),
            ("series: [{token: '1'}]", "exactly"),
            ("series: [{token: '1', symbol: a, name: b, kind: swap}]", "swap"),
            (": : :", "not valid YAML"),
        ],
    )
    def test_a_malformed_file_is_refused(self, tmp_path: Path, text: str, message: str) -> None:
        with pytest.raises(ConfigurationError, match=message):
            ReferenceSeriesCatalog.load(self.write(tmp_path, text))

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="cannot read"):
            ReferenceSeriesCatalog.load(tmp_path / "none.yaml")

    def test_selecting_names_picks_them_in_order_and_an_unknown_one_is_an_error(self) -> None:
        catalog = ReferenceSeriesCatalog.load(DEFAULT_CATALOG_FILE)

        assert [s.symbol for s in catalog.select(["India VIX", "Nifty 50"])] == [
            "India VIX",
            "Nifty 50",
        ]
        assert len(catalog.select([])) == 15
        with pytest.raises(ConfigurationError, match="Nifty 51"):
            catalog.select(["Nifty 51"])


class TestVerifier:
    def test_a_token_that_moved_is_caught_before_it_fetches_the_wrong_series(self) -> None:
        rows = [r for r in master_rows() if r["token"] != "99926000"]

        with pytest.raises(ReferenceSeriesMismatch, match="no NSE index row"):
            ReferenceSeriesVerifier().verify([NIFTY], rows)

    def test_a_renamed_series_is_caught(self) -> None:
        rows = [
            {**r, "symbol": "Nifty Fifty"} if r["token"] == "99926000" else r for r in master_rows()
        ]

        with pytest.raises(ReferenceSeriesMismatch, match="Nifty Fifty"):
            ReferenceSeriesVerifier().verify([NIFTY], rows)

    def test_a_cash_row_with_the_same_token_does_not_count_as_the_index(self) -> None:
        rows = [{**r, "instrumenttype": ""} for r in master_rows()]

        with pytest.raises(ReferenceSeriesMismatch):
            ReferenceSeriesVerifier().verify([NIFTY], rows)

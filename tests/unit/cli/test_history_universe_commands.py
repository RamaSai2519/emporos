"""EM-191 D1: the universe fetch resolves what the master knows, skips what it does not, batches
predictably and reads the committed lists."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.cli.history_universe_commands import batches, resolve_known, universe_symbols
from emporos.instruments.cache import InstrumentCache
from tests.support.fakes import make_instrument

ROOT = Path(__file__).resolve().parents[3] / "config" / "universe" / "d1"


class TestResolveKnown:
    def test_known_names_resolve_and_unknown_ones_are_reported(self) -> None:
        cache = InstrumentCache(
            [make_instrument("3045", symbol="SBIN-EQ"), make_instrument("11536", symbol="TCS-EQ")]
        )

        known, unknown = resolve_known(cache, ["SBIN-EQ", "GONE-EQ", "TCS-EQ"])

        assert [i.tradingsymbol for i in known] == ["SBIN-EQ", "TCS-EQ"]
        assert unknown == ["GONE-EQ"]

    def test_nothing_known_is_not_an_error_here(self) -> None:
        assert resolve_known(InstrumentCache([]), ["A-EQ"]) == ([], ["A-EQ"])


class TestBatches:
    def test_the_last_batch_may_be_short(self) -> None:
        items = [make_instrument(str(1000 + n), symbol=f"S{n}-EQ") for n in range(5)]

        assert [len(b) for b in batches(items, 2)] == [2, 2, 1]

    def test_an_empty_universe_has_no_batches(self) -> None:
        assert batches([], 3) == []

    def test_a_batch_must_hold_a_name(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            batches([], 0)


class TestCommittedUniverse:
    def test_the_universe_is_the_two_lists_once_each(self) -> None:
        symbols = universe_symbols(ROOT)

        assert len(symbols) == 250
        assert "RELIANCE-EQ" in symbols and all(s.endswith("-EQ") for s in symbols)

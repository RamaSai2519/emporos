"""EM-248 E2: index crossings as signals: the same rule and path as the ledger's `index` kind."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from tests.unit.research.atlas.synthetic import market_and_stock, panel_from, sessions

from emporos.research.atlas.crossing_ledger import CrossingLedgerBuilder
from emporos.research.atlas.crossings import CrossingRules
from emporos.research.atlas.index_signals import (
    IndexCrossing,
    IndexSignalBuilder,
    index_signal_lines,
    write_index_signals,
)
from emporos.research.atlas.ledger import MoveLedgerBuilder
from emporos.research.cause_ledger.groups import YamlGroupMap

NIFTY = "NSE:99926000"
BANK = "NSE:99926009"
RULES = CrossingRules(first_day=sessions()[0])


def panels() -> dict[str, object]:
    market, gaps, idio, idio_gaps = market_and_stock(3)
    bank = market * 1.3 + idio
    bank[250, 6:10] += 0.004  # BANKNIFTY alone runs up on day 250
    return {
        NIFTY: panel_from(market, gaps),
        BANK: panel_from(bank, gaps * 1.2 + idio_gaps),
    }


class TestIndexSignals:
    def test_nifty_signals_are_exactly_the_ledgers_index_crossings(self) -> None:
        days = sessions()
        p = panels()
        ledger = MoveLedgerBuilder(days, YamlGroupMap([]))
        fits = ledger.fit({NIFTY: p[NIFTY]}, NIFTY, {}, {})  # type: ignore[dict-item]
        block = CrossingLedgerBuilder(days, RULES).build(fits)
        cols = block.columns
        theirs = sorted(
            (cols["day"][i], cols["direction"][i], cols["k"][i], cols["crossed_minute"][i])
            for i in range(len(block))
            if cols["kind"][i] == "index"
        )

        ours = IndexSignalBuilder(days, RULES).build({"NIFTY": NIFTY}, p)  # type: ignore[arg-type]

        assert theirs and theirs == sorted(
            (c.day, c.direction, c.k, c.crossed_minute) for c in ours
        )

    def test_banknifty_gets_its_own_crossing_on_its_own_path(self) -> None:
        days = sessions()
        found = IndexSignalBuilder(days, RULES).build(
            {"NIFTY": NIFTY, "BANKNIFTY": BANK},
            panels(),  # type: ignore[arg-type]
        )

        up = [
            c for c in found
            if c.name == "BANKNIFTY" and c.day == days[250] and c.k == 1.5 and c.direction == 1
        ]  # fmt: skip
        assert len(up) == 1 and up[0].instrument_id == BANK
        assert 9 * 60 + 15 + 6 * 5 <= up[0].crossed_minute <= 9 * 60 + 15 + 11 * 5  # in the burst
        assert {c.name for c in found} == {"NIFTY", "BANKNIFTY"}

    def test_the_file_and_the_report_carry_no_forward_figure(self, tmp_path: Path) -> None:
        crossings = [
            IndexCrossing("NIFTY", NIFTY, sessions()[10], 1, 1.5, True, 9 * 60 + 20),
            IndexCrossing("BANKNIFTY", BANK, sessions()[11], -1, 1.5, False, 11 * 60),
            IndexCrossing("NIFTY", NIFTY, sessions()[12], -1, 2.0, False, 13 * 60 + 5),
        ]
        path = tmp_path / "x" / "signals.parquet"

        assert write_index_signals(path, crossings) == 3
        table = pq.read_table(path)
        assert set(table.column_names) == {
            "name", "instrument_id", "day", "direction", "k", "at_open", "crossed_minute",
        }  # fmt: skip
        text = "\n".join(index_signal_lines(crossings))
        assert "2018-01" in text and "NIFTY@1.5" in text and "BANKNIFTY@1.5" in text
        assert "at open" in text and not any(w in text for w in ("resid", "drift", "return"))

    def test_the_result_is_deterministic_and_time_ordered(self) -> None:
        days = sessions()
        first = IndexSignalBuilder(days, RULES).build({"BANKNIFTY": BANK}, panels())  # type: ignore[arg-type]
        again = IndexSignalBuilder(days, RULES).build({"BANKNIFTY": BANK}, panels())  # type: ignore[arg-type]

        assert first == again and first == sorted(
            first, key=lambda c: (c.day, c.crossed_minute, c.name, c.direction, c.k)
        )
        assert np.all([c.direction in (1, -1) for c in first])

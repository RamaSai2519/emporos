"""EM-223: the Track A ledger: one line per arm, once, counted by program-wide N."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.research.swing.support import ZERO_SCHEDULE, dataset, hold, series, sessions

from emporos.core.errors import ConfigurationError
from emporos.research.screen_ledger import JsonlScreenLedger, ScreenLedgerCounter
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.ledger import (
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
    SwingIdentity,
    SwingRecord,
)
from emporos.research.swing.screen import ArmOutcome, SwingScreenRun, judge
from emporos.research.swing.simulator import SwingConfig

X = "NSE:1"
DAYS = sessions(40)


def identity(**over: object) -> SwingIdentity:
    base = SwingIdentity(
        "a1-momentum", {"top": "10", "rebalance": "monthly"}, "d1-148-x",
        date(2016, 10, 3), date(2024, 12, 31), Decimal(100000), 10,
    )  # fmt: skip
    return replace(base, **over)  # type: ignore[arg-type]


def outcome() -> ArmOutcome:
    prices = [(str(100 + i), str(101 + i)) for i in range(len(DAYS))]
    data = dataset(series(X, DAYS, prices))
    screen = SwingScreenRun(
        data, ZERO_SCHEDULE, SwingScreenRun.universe(data, ZERO_SCHEDULE),
        bootstrap=BlockBootstrap(paths=10),
    )  # fmt: skip
    return screen.run(lambda: hold(X, DAYS[0], DAYS[-2]), SwingConfig(Decimal(10000), 1))


def record(ident: SwingIdentity, pnl_file: str = "pnl/x.csv") -> SwingRecord:
    result = outcome()
    return SwingRecord(
        ident, result, judge(result, None), None, datetime(2026, 9, 25, tzinfo=UTC), pnl_file
    )


class TestIdentity:
    def test_the_id_is_stable_and_names_what_was_looked_at(self) -> None:
        assert identity().screen_id == identity().screen_id
        assert identity().screen_id.startswith("SWG-")

    @pytest.mark.parametrize(
        "change",
        [
            {"hypothesis": "a2"}, {"parameters": {"top": "5", "rebalance": "monthly"}},
            {"universe": "other"}, {"first_day": date(2017, 1, 1)}, {"last_day": date(2025, 1, 1)},
            {"capital": Decimal(50000)}, {"max_positions": 5},
        ],
    )  # fmt: skip
    def test_any_change_of_what_was_looked_at_is_a_new_look(
        self, change: dict[str, object]
    ) -> None:
        assert identity(**change).screen_id != identity().screen_id

    def test_parameter_order_does_not_matter(self) -> None:
        a = identity(parameters={"x": "1", "y": "2"})
        b = identity(parameters={"y": "2", "x": "1"})

        assert a.screen_id == b.screen_id


class TestLedger:
    def test_an_arm_is_appended_once(self, tmp_path: Path) -> None:
        ledger = JsonlSwingLedger(tmp_path / "screens.jsonl")

        assert ledger.record(record(identity())) is True
        assert ledger.record(record(identity())) is False
        assert ledger.count() == 1
        assert len((tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()) == 1

    def test_the_line_carries_the_numbers_the_head_needs(self, tmp_path: Path) -> None:
        path = tmp_path / "screens.jsonl"
        JsonlSwingLedger(path).record(record(identity()))

        line = json.loads(path.read_text(encoding="utf-8"))

        for key in (
            "screen_id", "net_cagr", "adverse_net_cagr", "net_sharpe", "universe_net_sharpe",
            "days_in_cash", "days", "round_trips", "max_drawdown", "p_drawdown_30",
            "positive_month_share", "failed_checks", "pnl_file", "bootstrap_seed", "track",
        ):  # fmt: skip
            assert key in line
        assert line["track"] == "swing"
        assert line["passed"] is False  # the neighbour check was not computed

    def test_a_second_ledger_object_sees_what_the_first_wrote(self, tmp_path: Path) -> None:
        path = tmp_path / "screens.jsonl"
        JsonlSwingLedger(path).record(record(identity()))

        assert JsonlSwingLedger(path).record(record(identity())) is False

    def test_a_malformed_line_is_an_error_not_a_silent_undercount(self, tmp_path: Path) -> None:
        path = tmp_path / "screens.jsonl"
        path.write_text('{"nope": 1}\n', encoding="utf-8")

        with pytest.raises(ConfigurationError, match="not a screen record"):
            JsonlSwingLedger(path).count()

    def test_program_wide_n_counts_these_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "screens.jsonl"
        ledger = JsonlSwingLedger(path)
        ledger.record(record(identity()))
        ledger.record(record(identity(hypothesis="a2")))

        counter = ScreenLedgerCounter(JsonlScreenLedger(path), "profit screens")

        assert counter.name == "profit screens"
        assert asyncio.run(counter.count()) == 2

    def test_the_default_ledger_is_the_committed_profit_file(self) -> None:
        assert Path("docs/research/profit/screens.jsonl") == DEFAULT_PROFIT_SCREENS


class TestPnlStore:
    def test_the_daily_series_round_trips(self, tmp_path: Path) -> None:
        store = DailyPnlStore(tmp_path)
        result = outcome()

        written = store.write("SWG-1", result)
        back = store.read(Path(written))

        assert written.endswith("SWG-1.csv")
        assert back == list(result.daily_pnl)
        assert sum(p for _, p in back) == result.arm.equity[-1] - result.arm.capital

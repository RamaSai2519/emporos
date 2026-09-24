"""EM-227: the L5 recipe reads the committed declaration's grid, loads the NIFTY 50 series before
scanning, and refuses to scan without it."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.cli.experiment_declarations import ExperimentDeclarationLoader
from emporos.cli.ranked_screen_commands import RANKED_CELLS, IndexTrendLeaderCell
from emporos.domain.candles import Candle
from emporos.research.partition import DISCOVERY, DataSplit
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.index_trend_leader import IndexTrendLeaderScan
from tests.unit.research.test_index_trend_leader import index_series

DECLARATION = Path("config/experiments/l5-index-trend-day-leader.yaml")


class FakeBars:
    def __init__(self, series: dict[str, list[Candle]]) -> None:
        self._series = series
        self.asked: list[tuple[str, DataSplit]] = []

    def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
        self.asked.append((instrument_id, split))
        return self._series.get(instrument_id, [])


class TestIndexTrendLeaderCell:
    def test_it_is_registered_under_the_declared_slug(self) -> None:
        assert isinstance(RANKED_CELLS["l5-index-trend-day-leader"], IndexTrendLeaderCell)

    def test_the_committed_declaration_gives_the_four_declared_arms(self) -> None:
        declaration = ExperimentDeclarationLoader().load(DECLARATION)

        points = IndexTrendLeaderCell().arms(declaration)

        assert [(p["index_move_min_pct"], p["rank_key"]) for p in points] == [
            ("0.4", "volume"),
            ("0.4", "move"),
            ("0.5", "volume"),
            ("0.5", "move"),
        ]
        assert declaration.position_value == Decimal(50_000)

    def test_prepare_reads_the_nifty_series_over_discovery_only(self) -> None:
        cell = IndexTrendLeaderCell()
        bars = FakeBars({"NSE:99926000": index_series({0: "101"})})

        cell.prepare(bars)
        scan = cell.scan(
            {"index_move_min_pct": "0.4", "rank_key": "volume"}, ScanExecution(Decimal(50_000))
        )

        assert bars.asked == [("NSE:99926000", DISCOVERY)]
        assert isinstance(scan, IndexTrendLeaderScan)

    def test_an_empty_index_series_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fetch-reference"):
            IndexTrendLeaderCell().prepare(FakeBars({}))

    def test_scanning_before_preparing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="before scanning"):
            IndexTrendLeaderCell().scan(
                {"index_move_min_pct": "0.4", "rank_key": "move"}, ScanExecution(Decimal(50_000))
            )

    def test_the_label_names_the_gate_and_the_key(self) -> None:
        label = IndexTrendLeaderCell().label({"index_move_min_pct": "0.5", "rank_key": "move"})

        assert label == "top1 index>=0.5% rank=move"

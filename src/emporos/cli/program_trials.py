"""Composition root for program-wide N (EM-191 F2): which stores count as looks.

Every Mongo trial ledger, the published experiment registry, the documented proof-run grids
that predate their Mongo ledgers (F2B) and every screen the fast screener has run (F3). A
curation that is not recording its trials still counts them: its own in-memory ledger joins as one
more source, so an exploratory run is priced at the same N it would have raised.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.robustness.historical_grids import (
    GridFamily,
    HistoricalGridCounter,
    HistoricalGridLoader,
)
from emporos.backtest.robustness.program_trials import (
    ProgramTrialCount,
    RegistryIndexCounter,
    StoreTrialCounter,
    TrialCounter,
)
from emporos.backtest.robustness.trials import TrialLedger
from emporos.cli.experiment_registry import DEFAULT_EXPERIMENTS_DIR, INDEX_JSON
from emporos.persistence.cross_sectional_ledger import MongoCrossSectionalTrialLedger
from emporos.persistence.feature_ledger import MongoFeatureTrialLedger
from emporos.persistence.lead_lag_ledger import MongoLeadLagTrialLedger
from emporos.persistence.trial_ledger import MongoTrialLedger
from emporos.research.screen_ledger import JsonlScreenLedger, ScreenLedgerCounter

STRATEGY_TRIALS = "strategy trials"
DEFAULT_HISTORICAL_GRIDS = Path("docs/research/edge-search/historical-trials.yaml")
DEFAULT_SCREENS = Path("docs/research/edge-search/screens.jsonl")


class ProgramTrialCountFactory:
    def __init__(
        self,
        experiments_dir: Path = DEFAULT_EXPERIMENTS_DIR,
        historical_grids: Path = DEFAULT_HISTORICAL_GRIDS,
        screens: Path = DEFAULT_SCREENS,
    ) -> None:
        self._experiments_dir = experiments_dir
        self._historical_grids = historical_grids
        self._screens = screens

    def build(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        unrecorded_run: TrialLedger | None = None,
    ) -> ProgramTrialCount:
        scored: dict[str, TrialLedger] = {STRATEGY_TRIALS: MongoTrialLedger(database)}
        if unrecorded_run is not None:
            scored["this run (unrecorded)"] = unrecorded_run
        grids = HistoricalGridLoader().load(self._historical_grids)
        counters: list[TrialCounter] = [
            StoreTrialCounter("feature trials", MongoFeatureTrialLedger(database)),
            StoreTrialCounter("cross-sectional trials", MongoCrossSectionalTrialLedger(database)),
            StoreTrialCounter("lead-lag trials", MongoLeadLagTrialLedger(database)),
            HistoricalGridCounter("feature grids (historical)", GridFamily.FEATURE, grids),
            HistoricalGridCounter(
                "cross-sectional grids (historical)", GridFamily.CROSS_SECTIONAL, grids
            ),
            HistoricalGridCounter("lead-lag grids (historical)", GridFamily.LEAD_LAG, grids),
            RegistryIndexCounter(self._experiments_dir / INDEX_JSON),
            ScreenLedgerCounter(JsonlScreenLedger(self._screens)),
        ]
        return ProgramTrialCount(scored, counters)

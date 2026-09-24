"""Composition root for program-wide N (EM-191 F2): which stores count as looks.

Every Mongo trial ledger and the published experiment registry. A curation that is not recording
its trials still counts them: its own in-memory ledger joins as one more source, so an exploratory
run is priced at the same N it would have raised.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

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

STRATEGY_TRIALS = "strategy trials"


class ProgramTrialCountFactory:
    def __init__(self, experiments_dir: Path = DEFAULT_EXPERIMENTS_DIR) -> None:
        self._experiments_dir = experiments_dir

    def build(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        unrecorded_run: TrialLedger | None = None,
    ) -> ProgramTrialCount:
        scored: dict[str, TrialLedger] = {STRATEGY_TRIALS: MongoTrialLedger(database)}
        if unrecorded_run is not None:
            scored["this run (unrecorded)"] = unrecorded_run
        counters: list[TrialCounter] = [
            StoreTrialCounter("feature trials", MongoFeatureTrialLedger(database)),
            StoreTrialCounter("cross-sectional trials", MongoCrossSectionalTrialLedger(database)),
            StoreTrialCounter("lead-lag trials", MongoLeadLagTrialLedger(database)),
            RegistryIndexCounter(self._experiments_dir / INDEX_JSON),
        ]
        return ProgramTrialCount(scored, counters)

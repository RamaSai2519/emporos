"""The experiments run before the ledger existed, reconstructed from the published records.

Nothing is invented. The curation records (`docs/strategies/*.json`) say which candidates were
tried in which windows and which was chosen, and what each chosen candidate netted out of sample;
they do not keep the training scores, so those trials are recorded with their figures unknown
rather than guessed. The momentum runs come from `docs/backtests/`. Trial ids are fixed, so
running the backfill twice adds nothing the second time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.experiments import Trial, TrialRole

CURATION_EXPERIMENT = "curation-2026-09"
MOMENTUM_EXPERIMENT = "momentum-acceptance-2026-09"
CURATION_DATASET = (
    "candles 5m 2025-09-22..2026-09-18, 29 liquid NSE symbols, universe from the current master"
)
MOMENTUM_DATASET = "candles 5m 2025-09-22..2026-09-18, RELIANCE-EQ and TCS-EQ"
COST_MODEL = "Angel One dated fee schedule (unverified), earliest schedule assumed; 5 bps buffer"
SOURCE_NOTE = "backfilled from the published record; recorded_at is when it was backfilled"

# Two more runs of the same data, reported in docs/backtests/README.md (no run ids were kept).
_MOMENTUM_VARIANTS = (
    ("no-buffer", Decimal("-18674.37"), "limit_buffer_bps 0"),
    ("paper-fill-defaults", Decimal("-28416.54"), "identical to the shipped run, per the README"),
)


class PastExperiments:
    def __init__(self, recorded_at: datetime, annualisation_days: int = 252) -> None:
        self._recorded_at = recorded_at
        self._root_days = DecimalMath.sqrt(Decimal(annualisation_days))

    def curation(self, records: Sequence[Mapping[str, Any]]) -> list[Trial]:
        trials: list[Trial] = []
        for record in records:
            strategy = str(record["strategy"])
            candidates = [str(c) for c in record["candidates"]]
            nets = record["out_of_sample"]["window_nets"]
            for index, (start, end, chosen) in enumerate(record["windows"]):
                trials.extend(self._train(strategy, index, candidates))
                trials.append(
                    self._test(strategy, index, str(chosen), Decimal(nets[index]), start, end)
                )
        return trials

    def momentum(self, document: Mapping[str, Any]) -> list[Trial]:
        run, metrics = document["run"], document["metrics"]
        acceptance = self._momentum(
            "as-shipped", run["run_id"], run["config_hash"], "the acceptance run"
        )
        acceptance = replace(
            acceptance,
            trade_count=int(metrics["trades"]["count"]),
            net_pnl=Decimal(metrics["trades"]["net_pnl"]),
            daily_sharpe=DecimalMath.divide(Decimal(metrics["returns"]["sharpe"]), self._root_days),
        )
        variants = [
            replace(self._momentum(name, None, None, note), net_pnl=net)
            for name, net, note in _MOMENTUM_VARIANTS
        ]
        return [acceptance, *variants]

    def _train(self, strategy: str, index: int, candidates: Sequence[str]) -> list[Trial]:
        return [
            self._trial(
                CURATION_EXPERIMENT,
                strategy,
                candidate,
                TrialRole.TRAIN,
                f"w{index}/train/{candidate}",
                CURATION_DATASET,
                note=f"training window {index}; score not kept in the record; {SOURCE_NOTE}",
            )
            for candidate in candidates
        ]

    def _test(
        self, strategy: str, index: int, chosen: str, net: Decimal, start: str, end: str
    ) -> Trial:
        return self._trial(
            CURATION_EXPERIMENT, strategy, chosen, TrialRole.TEST, f"w{index}/test/{chosen}",
            CURATION_DATASET, net_pnl=net,
            note=f"test window {start}..{end}; trades and Sharpe are pooled, not kept per window; "
            f"{SOURCE_NOTE}",
        )  # fmt: skip

    def _momentum(self, name: str, run_id: str | None, config_hash: str | None, note: str) -> Trial:
        return self._trial(
            MOMENTUM_EXPERIMENT, "momentum_v1", name, TrialRole.STANDALONE, f"momentum_v1/{name}",
            MOMENTUM_DATASET, run_id=run_id, config_hash=config_hash, note=f"{note}; {SOURCE_NOTE}",
        )  # fmt: skip

    def _trial(
        self, experiment: str, strategy: str, candidate: str, role: TrialRole, path: str,
        dataset: str, *, run_id: str | None = None, config_hash: str | None = None,
        net_pnl: Decimal | None = None, note: str = "",
    ) -> Trial:  # fmt: skip
        prefix = "" if experiment == MOMENTUM_EXPERIMENT else f"{strategy}/"
        return Trial(
            trial_id=f"{experiment}/backfill/{prefix}{path}", experiment=experiment,
            strategy=strategy, candidate=candidate, role=role, dataset_version=dataset,
            config_hash=config_hash, cost_model=COST_MODEL, recorded_at=self._recorded_at,
            run_id=run_id, net_pnl=net_pnl, note=note,
        )  # fmt: skip

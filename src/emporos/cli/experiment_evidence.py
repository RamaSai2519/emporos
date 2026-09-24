"""Published experiment reports (`docs/strategies/experiments/EXP-*.json`) as graduation evidence.

Reads what the registry wrote and nothing else: a report that is missing a field graduation needs
is reported as not having it (None), never filled in. In particular `assumed_instrument_ids` lives
under `supporting.data_provenance`, which no report builder emits yet, so every report currently
reads as "provenance not recorded" and `DataIntegrityClean` refuses. That is deliberate.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from emporos.cli.experiment_registry import DEFAULT_EXPERIMENTS_DIR
from emporos.domain.research_experiments import ExperimentOutcomeLabel
from emporos.graduation.ports import ExperimentEvidenceView


class FileExperimentEvidence:
    def __init__(self, root: Path = DEFAULT_EXPERIMENTS_DIR) -> None:
        self._root = root

    async def get(self, experiment_id: str) -> ExperimentEvidenceView | None:
        path = self._root / f"{experiment_id}.json"
        if not path.is_file() or path.name != f"{experiment_id}.json":
            return None
        return self._view(json.loads(path.read_text(encoding="utf-8")))

    async def latest_for(self, behaviour_hash: str, family: str) -> ExperimentEvidenceView | None:
        matching = [
            v for v in self._all() if v.family == family and behaviour_hash in v.behaviour_hashes
        ]
        if not matching:
            return None
        return max(matching, key=lambda v: (v.declared_at, v.experiment_id))

    def _all(self) -> list[ExperimentEvidenceView]:
        return [
            self._view(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self._root.glob("EXP-*.json"))
        ]

    @staticmethod
    def _view(document: dict[str, Any]) -> ExperimentEvidenceView:
        versions = document.get("versions") or {}
        hashes = {h for h in [versions.get("behaviour_hash")] if h}
        hashes |= set((versions.get("candidate_behaviour_hashes") or {}).values())
        dataset = versions.get("dataset") or {}
        provenance = (document.get("supporting") or {}).get("data_provenance") or {}
        assumed = provenance.get("assumed_instrument_ids")
        return ExperimentEvidenceView(
            experiment_id=document["experiment_id"],
            family=document["family"],
            outcome=ExperimentOutcomeLabel(document["outcome"]),
            declared_at=datetime.fromisoformat(document["declaration"]["declared_at"]),
            behaviour_hashes=frozenset(hashes),
            holdout_reserved=(document.get("periods") or {}).get("holdout") is not None,
            unsettled_reason_codes=frozenset(
                r["code"]
                for r in document.get("reasons", [])
                if r["outcome"] != "pass" and r.get("code")
            ),
            assumed_instrument_ids=None if assumed is None else tuple(assumed),
            quarantine_hash=dataset.get("quarantine_hash"),
        )

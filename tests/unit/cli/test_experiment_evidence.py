"""Published experiment reports read as graduation evidence: missing means missing (EM-189)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from emporos.cli.experiment_evidence import FileExperimentEvidence
from emporos.cli.experiment_registry import DEFAULT_EXPERIMENTS_DIR
from emporos.domain.research_experiments import ExperimentOutcomeLabel

HASH = "abcdef0123456789"


def report(
    experiment_id: str = "EXP-20260924-orb-v1-abcd1234",
    family: str = "strategy",
    declared: str = "2026-09-24T04:00:00+00:00",
    **overrides: Any,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "experiment_id": experiment_id,
        "family": family,
        "outcome": "accepted",
        "declaration": {"declared_at": declared},
        "versions": {
            "behaviour_hash": HASH,
            "candidate_behaviour_hashes": {},
            "dataset": {"quarantine_hash": "q-hash"},
        },
        "periods": {"holdout": {"first": "2026-08-01", "last": "2026-08-28"}},
        "reasons": [
            {"code": "too_few_trades", "outcome": "pass"},
            {"code": "holdout_not_evaluated", "outcome": "unknown"},
        ],
        "supporting": {"data_provenance": {"assumed_instrument_ids": ["NSE:1"]}},
    }
    document.update(overrides)
    return document


def publish(root: Path, document: dict[str, Any]) -> None:
    (root / f"{document['experiment_id']}.json").write_text(json.dumps(document))


async def test_a_published_report_becomes_a_view_of_what_graduation_judges(tmp_path: Path) -> None:
    publish(tmp_path, report())

    view = await FileExperimentEvidence(tmp_path).get("EXP-20260924-orb-v1-abcd1234")

    assert view is not None
    assert view.outcome is ExperimentOutcomeLabel.ACCEPTED and view.family == "strategy"
    assert view.behaviour_hashes == frozenset({HASH})
    assert view.holdout_reserved is True
    assert view.unsettled_reason_codes == frozenset({"holdout_not_evaluated"})
    assert view.assumed_instrument_ids == ("NSE:1",) and view.quarantine_hash == "q-hash"


async def test_what_a_report_did_not_record_reads_as_not_recorded(tmp_path: Path) -> None:
    bare = report(
        versions={"behaviour_hash": None, "candidate_behaviour_hashes": {}, "dataset": None},
        periods={"holdout": None},
        supporting={},
    )
    publish(tmp_path, bare)

    view = await FileExperimentEvidence(tmp_path).get("EXP-20260924-orb-v1-abcd1234")

    assert view is not None
    assert view.behaviour_hashes == frozenset() and view.holdout_reserved is False
    assert view.assumed_instrument_ids is None and view.quarantine_hash is None


async def test_an_unknown_or_path_like_id_is_not_found(tmp_path: Path) -> None:
    evidence = FileExperimentEvidence(tmp_path)
    assert await evidence.get("EXP-nope") is None
    assert await evidence.get("../secret") is None


async def test_a_portfolio_report_matches_any_of_its_candidate_hashes_and_the_newest_wins(
    tmp_path: Path,
) -> None:
    jev_versions = {"behaviour_hash": None, "candidate_behaviour_hashes": {"orb_v1": HASH}}
    old = report("EXP-20260901-jev-aaaa1111", "jev_incremental", "2026-09-01T00:00:00+00:00")
    new = report("EXP-20260920-jev-bbbb2222", "jev_incremental", "2026-09-20T00:00:00+00:00")
    other = report("EXP-20260922-orb-v1-cccc3333", "strategy", "2026-09-22T00:00:00+00:00")
    for document in (old, new, other):
        document["versions"] = (
            {**document["versions"], **jev_versions}
            if "jev" in document["experiment_id"]
            else document["versions"]
        )
        publish(tmp_path, document)
    evidence = FileExperimentEvidence(tmp_path)

    latest = await evidence.latest_for(HASH, "jev_incremental")

    assert latest is not None and latest.experiment_id == "EXP-20260920-jev-bbbb2222"
    assert await evidence.latest_for("0" * 16, "jev_incremental") is None


async def test_every_shipped_published_report_reads_and_none_can_pass_data_integrity() -> None:
    """The committed registry parses, and no report records its data provenance yet, so
    `DataIntegrityClean` will refuse every one of them: fail closed, by construction."""
    evidence = FileExperimentEvidence(DEFAULT_EXPERIMENTS_DIR)
    ids = [p.stem for p in sorted(DEFAULT_EXPERIMENTS_DIR.glob("EXP-*.json"))]
    assert ids

    views = [await evidence.get(i) for i in ids]

    assert all(v is not None and v.assumed_instrument_ids is None for v in views)

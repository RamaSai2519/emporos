"""A verdict describes one configuration; once the config changes it is stale, not rejected."""

from __future__ import annotations

from datetime import UTC, datetime

from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict, Standing, standing_of

HASH = "sha256:aaa"


def recorded(verdict: Verdict, behaviour_hash: str = HASH) -> RecordedVerdict:
    return RecordedVerdict(
        strategy="orb_v1",
        behaviour_hash=behaviour_hash,
        verdict=verdict,
        gates=(
            GateFinding("profit after costs is real", "fail", "P(net > 0) = 0.00"),
            GateFinding("beats the luck of the search", "unknown", "too few trials"),
            GateFinding("drawdown within the budget", "pass", "3.1% of 10%"),
        ),
        capital="50000",
        first_day="2025-09-22",
        last_day="2026-09-18",
        experiment="em118",
        source="curation",
        recorded_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def test_a_verdict_for_the_current_config_is_its_standing() -> None:
    for verdict in Verdict:
        assert standing_of(recorded(verdict), HASH) is Standing(verdict.value)


def test_a_verdict_for_another_config_is_stale_whatever_it_said() -> None:
    assert standing_of(recorded(Verdict.VALIDATED), "sha256:other") is Standing.STALE
    assert standing_of(recorded(Verdict.REJECTED), "sha256:other") is Standing.STALE


def test_no_verdict_is_its_own_standing() -> None:
    assert standing_of(None, HASH) is Standing.NONE


def test_failing_and_unresolved_gates_are_told_apart() -> None:
    verdict = recorded(Verdict.REJECTED)

    assert [g.name for g in verdict.failing] == ["profit after costs is real"]
    assert [g.name for g in verdict.unresolved] == ["beats the luck of the search"]

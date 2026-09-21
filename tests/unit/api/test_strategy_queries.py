"""What the dashboard is told about each strategy: whether it is running, and where its recorded
verdict stands against the config it would run today."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporos.api.queries import QueryService
from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict
from emporos.persistence.records import StrategyRecord, StrategyRunRecord

NOW = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)
HASH = "sha256:current"


class Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def find(
        self, query: dict[str, Any], *, sort: list[tuple[str, int]] | None = None, limit: int = 0
    ) -> list[Any]:
        rows = [r for r in self._rows if all(getattr(r, k, None) == v for k, v in query.items())]
        return rows[:limit] if limit else rows

    async def get(self, record_id: str) -> Any | None:
        return next((r for r in self._rows if r.id == record_id), None)

    async def count(self, query: dict[str, Any] | None = None) -> int:
        return len(await self.find(query or {}))


class Verdicts:
    def __init__(self, verdicts: dict[str, RecordedVerdict]) -> None:
        self._verdicts = verdicts

    async def latest_of_each(self) -> dict[str, RecordedVerdict]:
        return self._verdicts


def verdict(strategy: str, outcome: Verdict, behaviour_hash: str = HASH) -> RecordedVerdict:
    return RecordedVerdict(
        strategy=strategy,
        behaviour_hash=behaviour_hash,
        verdict=outcome,
        gates=(GateFinding("profit after costs is real", "fail", "net -3136"),),
        capital="50000",
        first_day="2025-09-22",
        last_day="2026-09-18",
        experiment="em118",
        source="curation",
        recorded_at=NOW,
    )


def strategy(name: str, *, enabled: bool = False, hashed: bool = True) -> StrategyRecord:
    return StrategyRecord(
        _id=f"id-{name}",
        name=name,
        config={"enabled": enabled},
        behaviour_hash=HASH if hashed else None,
    )


def run(name: str, stopped: bool) -> StrategyRunRecord:
    return StrategyRunRecord(
        _id=f"run-{name}",
        strategy_id=f"id-{name}",
        session_date="2026-09-21",
        created_at=NOW,
        stopped_at=NOW if stopped else None,
    )


def service(
    strategies: list[StrategyRecord],
    runs: list[StrategyRunRecord],
    verdicts: dict[str, RecordedVerdict],
) -> QueryService:
    none = Rows([])
    return QueryService(
        "paper",
        orders=none, events=none, executions=none, positions=none, snapshots=none,
        strategies=Rows(strategies), runs=Rows(runs), signals=none, risk_events=none,
        reconciliations=none, system_events=none, verdicts=Verdicts(verdicts),
        kill_switch=none, commands=none, results=none, risk_limits={},
    )  # fmt: skip


async def test_a_strategy_that_never_ran_and_was_never_curated_is_stopped_with_no_verdict() -> None:
    (dto,) = await service([strategy("orb_v1")], [], {}).strategies()

    assert dto.status == "stopped" and dto.standing == "none" and dto.verdict is None
    assert dto.enabled is False


async def test_a_run_that_has_not_stopped_is_running_and_a_stopped_one_is_not() -> None:
    live = await service([strategy("a")], [run("a", stopped=False)], {}).strategies()
    done = await service([strategy("a")], [run("a", stopped=True)], {}).strategies()

    assert live[0].status == "running" and done[0].status == "stopped"


async def test_the_verdict_and_why_travel_with_the_strategy() -> None:
    (dto,) = await service(
        [strategy("orb_v1")], [], {"orb_v1": verdict("orb_v1", Verdict.REJECTED)}
    ).strategies()

    assert dto.standing == "rejected"
    assert dto.verdict is not None and dto.verdict.outcome == "rejected"
    assert dto.verdict.gates[0].name == "profit after costs is real"
    assert dto.verdict.capital == "50000" and dto.verdict.experiment == "em118"


async def test_a_verdict_for_an_edited_config_is_stale_even_when_it_said_validated() -> None:
    old = verdict("orb_v1", Verdict.VALIDATED, behaviour_hash="sha256:before-the-edit")

    (dto,) = await service([strategy("orb_v1")], [], {"orb_v1": old}).strategies()

    assert dto.standing == "stale"
    assert dto.verdict is not None and dto.verdict.outcome == "validated"  # still shown, labelled


async def test_a_catalogue_row_with_no_behaviour_hash_cannot_claim_a_verdict() -> None:
    verdicts = {"orb_v1": verdict("orb_v1", Verdict.VALIDATED)}

    (dto,) = await service([strategy("orb_v1", hashed=False)], [], verdicts).strategies()

    assert dto.standing == "stale"


async def test_enabled_comes_from_the_strategys_own_config() -> None:
    (dto,) = await service([strategy("orb_v1", enabled=True)], [], {}).strategies()

    assert dto.enabled is True

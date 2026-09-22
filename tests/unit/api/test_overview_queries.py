"""EM-143 — the dashboard's overview must show THIS account's session, never another one's, even
when another account's worker transitioned state more recently in the same shared collection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporos.api.queries import QueryService
from emporos.domain.money import Money
from emporos.persistence.records import PortfolioSnapshotRecord, SystemEventRecord

NOW = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)


class Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def find(
        self, query: dict[str, Any], *, sort: list[tuple[str, int]] | None = None, limit: int = 0
    ) -> list[Any]:
        rows = [r for r in self._rows if all(getattr(r, k, None) == v for k, v in query.items())]
        if sort:
            for field, direction in reversed(sort):
                rows.sort(key=lambda r: getattr(r, field), reverse=direction < 0)
        return rows[:limit] if limit else rows

    async def get(self, record_id: str) -> Any | None:
        return next((r for r in self._rows if r.id == record_id), None)

    async def count(self, query: dict[str, Any] | None = None) -> int:
        return len(await self.find(query or {}))


class Verdicts:
    async def latest_of_each(self) -> dict[str, Any]:
        return {}


def state_event(account_id: str, to: str, ts: datetime) -> SystemEventRecord:
    return SystemEventRecord.model_validate(
        {
            "_id": f"evt-{account_id}-{to}",
            "type": "session_state",
            "account_id": account_id,
            "ts": ts,
            "from": "TRADING",
            "to": to,
        }
    )


def health_event(
    account_id: str, ts: datetime, mode: str = "paper", broker: bool = True, feed: bool = True
) -> SystemEventRecord:
    return SystemEventRecord.model_validate(
        {
            "_id": f"health-{account_id}-{ts.isoformat()}",
            "type": "worker_health",
            "account_id": account_id,
            "ts": ts,
            "trading_mode": mode,
            "broker_healthy": broker,
            "feed_healthy": feed,
        }
    )


def snapshot(account_id: str, ts: datetime, cash: str = "90000") -> PortfolioSnapshotRecord:
    return PortfolioSnapshotRecord(
        _id=f"snap-{account_id}-{ts.isoformat()}",
        account_id=account_id,
        ts=ts,
        kind="INTRADAY",
        cash=Money.of(cash),
        realised_pnl=Money.of("-2.5"),
        unrealised_pnl=Money.of("4.5"),
        fees=Money.of("2.5"),
        trades=3,
    )


def service(
    account_id: str,
    system_events: list[SystemEventRecord],
    snapshots: list[PortfolioSnapshotRecord] | None = None,
) -> QueryService:
    none = Rows([])
    return QueryService(
        account_id,
        orders=none, events=none, executions=none, positions=none, snapshots=Rows(snapshots or []),
        strategies=none, runs=none, signals=none, risk_events=none,
        reconciliations=none, system_events=Rows(system_events), verdicts=Verdicts(),
        kill_switch=none, commands=none, results=none, risk_limits={},
    )  # fmt: skip


async def test_overview_reports_this_accounts_own_session_state() -> None:
    events = [state_event("paper", "TRADING", NOW)]

    overview = await service("paper", events).overview()

    assert overview.session_state == "TRADING"


async def test_overview_never_shows_another_accounts_more_recent_session_state() -> None:
    events = [
        state_event("paper", "SQUARING_OFF", NOW),
        state_event("live", "TRADING", datetime(2026, 9, 21, 5, 0, tzinfo=UTC)),  # later, not ours
    ]

    overview = await service("paper", events).overview()

    assert overview.session_state == "SQUARING_OFF"


async def test_overview_has_no_session_state_when_this_account_never_ran() -> None:
    events = [state_event("live", "TRADING", NOW)]

    overview = await service("paper", events).overview()

    assert overview.session_state is None


async def test_overview_reports_trading_mode_and_health_from_the_latest_report() -> None:
    events = [health_event("paper", NOW, mode="live", broker=True, feed=False)]

    overview = await service("paper", events).overview()

    assert (overview.trading_mode, overview.broker_healthy, overview.feed_healthy) == (
        "live", True, False,
    )  # fmt: skip


async def test_overview_never_shows_another_accounts_health_report() -> None:
    events = [health_event("live", NOW, mode="live")]

    overview = await service("paper", events).overview()

    assert (overview.trading_mode, overview.broker_healthy, overview.feed_healthy) == (
        None, None, None,
    )  # fmt: skip


async def test_overview_shows_the_most_recent_health_report_not_the_first() -> None:
    events = [
        health_event("paper", NOW, mode="paper", broker=True),
        health_event(
            "paper", datetime(2026, 9, 21, 5, 0, tzinfo=UTC), mode="paper", broker=False
        ),
    ]

    overview = await service("paper", events).overview()

    assert overview.broker_healthy is False


async def test_worker_healthy_follows_session_state() -> None:
    trading = await service("paper", [state_event("paper", "TRADING", NOW)]).overview()
    halted = await service("paper", [state_event("paper", "HALTED", NOW)]).overview()
    failed = await service("paper", [state_event("paper", "FAILED", NOW)]).overview()
    unknown = await service("paper", []).overview()

    assert trading.worker_healthy is True
    assert halted.worker_healthy is False
    assert failed.worker_healthy is False
    assert unknown.worker_healthy is None


async def test_overview_reports_cash_from_the_latest_own_take_snapshot() -> None:
    later = datetime(2026, 9, 22, 4, 30, tzinfo=UTC)
    snapshots = [
        snapshot("paper", NOW, cash="90000"),
        snapshot("paper", later, cash="89400.50"),
    ]

    overview = await service("paper", [], snapshots).overview()

    assert overview.cash == "89400.50"


async def test_overview_reports_none_when_this_account_has_no_take_snapshot() -> None:
    snapshots = [snapshot("live", NOW, cash="90000")]

    overview = await service("paper", [], snapshots).overview()

    assert overview.cash is None

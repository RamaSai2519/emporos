"""EM-143 — the dashboard's overview must show THIS account's session, never another one's, even
when another account's worker transitioned state more recently in the same shared collection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporos.api.queries import QueryService
from emporos.persistence.records import SystemEventRecord

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


def service(account_id: str, system_events: list[SystemEventRecord]) -> QueryService:
    none = Rows([])
    return QueryService(
        account_id,
        orders=none, events=none, executions=none, positions=none, snapshots=none,
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

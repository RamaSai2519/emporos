"""Read-only queries behind the dashboard, over the persistence repositories.

The API process holds no broker credentials and imports no broker code, so everything it can show
is what the worker has PERSISTED. Nothing here computes P&L: it reports the worker's numbers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from emporos.api.models import (
    CommandDetailDto,
    CommandDto,
    CommandResultDto,
    ExecutionDto,
    KillSwitchDto,
    OrderDetailDto,
    OrderDto,
    OrderEventDto,
    OverviewDto,
    PositionDto,
    ReconciliationDto,
    RiskDto,
    RiskEventDto,
    StrategyDto,
    SystemEventDto,
)
from emporos.domain.money import Money
from emporos.persistence.records import (
    CommandRecord,
    CommandResultRecord,
    ExecutionRecord,
    KillSwitchRecord,
    OrderEventRecord,
    OrderRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    ReconciliationRunRecord,
    RiskEventRecord,
    SignalRecord,
    StrategyRecord,
    StrategyRunRecord,
    SystemEventRecord,
)

MAX_PAGE = 500


class Reader(Protocol):
    async def find(
        self,
        query: dict[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[Any]: ...

    async def get(self, record_id: str) -> Any | None: ...

    async def count(self, query: dict[str, Any] | None = None) -> int: ...


def _m(value: Money | None) -> str | None:
    return None if value is None else format(value.amount, "f")


def _ms(value: Money) -> str:
    return format(value.amount, "f")


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE))


class QueryService:
    def __init__(
        self,
        account_id: str,
        *,
        orders: Reader,
        events: Reader,
        executions: Reader,
        positions: Reader,
        snapshots: Reader,
        strategies: Reader,
        runs: Reader,
        signals: Reader,
        risk_events: Reader,
        reconciliations: Reader,
        system_events: Reader,
        kill_switch: Reader,
        commands: Reader,
        results: Reader,
        risk_limits: dict[str, str],
    ) -> None:
        self._account = account_id
        self._orders, self._events, self._executions = orders, events, executions
        self._positions, self._snapshots = positions, snapshots
        self._strategies, self._runs, self._signals = strategies, runs, signals
        self._risk_events, self._reconciliations = risk_events, reconciliations
        self._system_events, self._kill_switch = system_events, kill_switch
        self._commands, self._results = commands, results
        self._limits = risk_limits

    async def overview(self) -> OverviewDto:
        state = await self._system_events.find(
            {"type": "session_state"}, sort=[("ts", -1)], limit=1
        )
        switch: KillSwitchRecord | None = await self._kill_switch.get("kill_switch")
        recon = await self._reconciliations.find({}, sort=[("ts", -1)], limit=1)
        snap: list[PortfolioSnapshotRecord] = await self._snapshots.find(
            {"account_id": self._account}, sort=[("ts", -1)], limit=1
        )
        open_positions = await self._positions.count(
            {"account_id": self._account, "net_quantity": {"$ne": 0}}
        )
        latest = snap[0] if snap else None
        return OverviewDto(
            session_state=state[0].model_dump().get("to") if state else None,
            session_state_at=state[0].ts if state else None,
            kill_switch=None if switch is None else self._switch(switch),
            reconciliation=self._reconciliation(recon[0]) if recon else None,
            open_positions=open_positions,
            realised_pnl=_m(latest.realised_pnl) if latest else None,
            unrealised_pnl=_m(latest.unrealised_pnl) if latest else None,
            fees=_m(latest.fees) if latest else None,
            trades=latest.trades if latest else None,
            snapshot_at=latest.ts if latest else None,
            pending_commands=await self._commands.count(
                {"status": {"$in": ["PENDING", "ACCEPTED", "EXECUTING"]}}
            ),
        )

    async def positions(self, open_only: bool = False) -> list[PositionDto]:
        query: dict[str, Any] = {"account_id": self._account}
        if open_only:
            query["net_quantity"] = {"$ne": 0}
        rows: list[PositionRecord] = await self._positions.find(query, sort=[("instrument_id", 1)])
        return [
            PositionDto(
                instrument_id=p.instrument_id,
                net_quantity=p.net_quantity,
                average_price=_ms(p.average_price),
                realised_pnl=_ms(p.realised_pnl),
                fees=_m(p.fees),
                updated_at=p.updated_at,
            )
            for p in rows
        ]

    async def orders(
        self, state: str | None = None, instrument_id: str | None = None, limit: int = 100
    ) -> list[OrderDto]:
        query: dict[str, Any] = {"account_id": self._account}
        if state:
            query["state"] = state
        if instrument_id:
            query["instrument_id"] = instrument_id
        rows: list[OrderRecord] = await self._orders.find(
            query, sort=[("created_at", -1)], limit=_clamp(limit)
        )
        return [self._order(o) for o in rows]

    async def order(self, order_id: str) -> OrderDetailDto | None:
        record: OrderRecord | None = await self._orders.get(order_id)
        if record is None or record.account_id != self._account:
            return None
        events: list[OrderEventRecord] = await self._events.find(
            {"order_id": order_id}, sort=[("seq", 1)]
        )
        return OrderDetailDto(
            order=self._order(record),
            events=[
                OrderEventDto(
                    seq=e.seq,
                    ts=e.ts,
                    state=e.state,
                    filled_quantity=e.filled_quantity,
                    reason=e.reason,
                )
                for e in events
            ],
        )

    async def executions(self, limit: int = 100) -> list[ExecutionDto]:
        rows: list[ExecutionRecord] = await self._executions.find(
            {"account_id": self._account}, sort=[("ts", -1)], limit=_clamp(limit)
        )
        return [
            ExecutionDto(
                id=e.id, broker_trade_id=e.broker_trade_id, order_id=e.order_id,
                instrument_id=e.instrument_id, side=e.side.value, quantity=e.quantity,
                price=_ms(e.price), fees=_m(e.fees), ts=e.ts,
            )
            for e in rows
        ]  # fmt: skip

    async def strategies(self) -> list[StrategyDto]:
        result: list[StrategyDto] = []
        rows: list[StrategyRecord] = await self._strategies.find({}, sort=[("name", 1)])
        for strategy in rows:
            runs: list[StrategyRunRecord] = await self._runs.find(
                {"strategy_id": strategy.id}, sort=[("created_at", -1)], limit=1
            )
            run = runs[0] if runs else None
            count = 0
            if run is not None:
                signals: list[SignalRecord] = await self._signals.find(
                    {"strategy_run_id": run.id}, limit=MAX_PAGE
                )
                count = len(signals)
            result.append(
                StrategyDto(
                    name=strategy.name,
                    config_hash=run.config_hash if run else None,
                    last_run_id=run.id if run else None,
                    last_run_date=run.session_date if run else None,
                    signals_last_run=count,
                )
            )
        return result

    async def risk(self, limit: int = 50) -> RiskDto:
        rows: list[RiskEventRecord] = await self._risk_events.find(
            {}, sort=[("ts", -1)], limit=_clamp(limit)
        )
        return RiskDto(
            limits=self._limits,
            recent_rejections=[
                RiskEventDto(
                    id=r.id, ts=r.ts, rule=r.rule, reason=r.reason, instrument_id=r.instrument_id,
                    strategy_run_id=r.strategy_run_id, signal_id=r.signal_id,
                )
                for r in rows
            ],
        )  # fmt: skip

    async def system_events(self, limit: int = 100) -> list[SystemEventDto]:
        rows: list[SystemEventRecord] = await self._system_events.find(
            {}, sort=[("ts", -1)], limit=_clamp(limit)
        )
        return [
            SystemEventDto(
                id=e.id,
                type=e.type,
                ts=e.ts,
                data={k: v for k, v in e.model_dump().items() if k not in {"id", "type", "ts"}},
            )
            for e in rows
        ]

    async def reconciliations(self, limit: int = 50) -> list[ReconciliationDto]:
        rows: list[ReconciliationRunRecord] = await self._reconciliations.find(
            {}, sort=[("ts", -1)], limit=_clamp(limit)
        )
        return [self._reconciliation(r) for r in rows]

    async def commands(self, limit: int = 50) -> list[CommandDto]:
        rows: list[CommandRecord] = await self._commands.find(
            {}, sort=[("created_at", -1)], limit=_clamp(limit)
        )
        return [self.command_dto(c) for c in rows]

    async def command(self, command_id: str) -> CommandDetailDto | None:
        record: CommandRecord | None = await self._commands.get(command_id)
        if record is None:
            return None
        results: list[CommandResultRecord] = await self._results.find(
            {"command_id": command_id}, sort=[("created_at", 1)]
        )
        return CommandDetailDto(
            command=self.command_dto(record),
            results=[
                CommandResultDto(ts=r.created_at, status=r.status, message=r.message, data=r.data)
                for r in results
            ],
        )

    async def changes_since(self, since: datetime) -> list[dict[str, Any]]:
        """Everything changed at or after `since`, oldest first: the catch-up behind the SSE stream.

        Inclusive on purpose: two changes can share a millisecond, and the caller de-duplicates.
        """
        changes: list[dict[str, Any]] = []
        for kind, reader, query in (
            ("order", self._orders, {"account_id": self._account, "updated_at": {"$gte": since}}),
            (
                "position",
                self._positions,
                {"account_id": self._account, "updated_at": {"$gte": since}},
            ),
            ("command", self._commands, {"updated_at": {"$gte": since}}),
        ):
            for record in await reader.find(query, sort=[("updated_at", 1)], limit=MAX_PAGE):
                changes.append({"kind": kind, "at": record.updated_at, "id": record.id,
                                "data": self._change_payload(kind, record)})  # fmt: skip
        return sorted(changes, key=lambda c: (c["at"], c["kind"], c["id"]))

    def _change_payload(self, kind: str, record: Any) -> dict[str, Any]:
        if kind == "order":
            return self._order(record).model_dump(mode="json")
        if kind == "command":
            return self.command_dto(record).model_dump(mode="json")
        return PositionDto(
            instrument_id=record.instrument_id, net_quantity=record.net_quantity,
            average_price=_ms(record.average_price), realised_pnl=_ms(record.realised_pnl),
            fees=_m(record.fees), updated_at=record.updated_at,
        ).model_dump(mode="json")  # fmt: skip

    # --- mapping ---------------------------------------------------------------------------
    @staticmethod
    def command_dto(c: CommandRecord) -> CommandDto:
        return CommandDto(
            id=c.id, idempotency_key=c.idempotency_key, type=c.type, status=c.status,
            params=c.params, issued_by=c.issued_by, created_at=c.created_at,
            updated_at=c.updated_at, expires_at=c.expires_at, reason=c.reason,
            attempts=c.attempts,
        )  # fmt: skip

    @staticmethod
    def _order(o: OrderRecord) -> OrderDto:
        return OrderDto(
            id=o.id, ordertag=o.ordertag, instrument_id=o.instrument_id, side=o.side.value,
            order_type=o.order_type.value, quantity=o.quantity, filled_quantity=o.filled_quantity,
            limit_price=_ms(o.limit_price), average_price=_m(o.average_price), state=o.state,
            strategy_run_id=o.strategy_run_id, signal_id=o.signal_id,
            parent_order_id=o.parent_order_id, reprice_count=o.reprice_count,
            status_message=o.status_message, created_at=o.created_at, updated_at=o.updated_at,
        )  # fmt: skip

    @staticmethod
    def _switch(s: KillSwitchRecord) -> KillSwitchDto:
        return KillSwitchDto(
            halted=s.halted, reason=s.reason, set_by=s.set_by, changed_at=s.changed_at
        )

    @staticmethod
    def _reconciliation(r: ReconciliationRunRecord) -> ReconciliationDto:
        return ReconciliationDto(
            id=r.id, ts=r.ts, status=r.status, trigger=r.trigger,
            discrepancies=r.discrepancies, healed=r.healed,
        )  # fmt: skip

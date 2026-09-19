"""The declarative MongoDB schema: every collection and index in plan.md §6.

Adding a collection or index is adding a spec here — the migration runner
never changes (open/closed). The unique indexes are the deliverable that
matters: they are the database-level last line of defence behind order and
fill idempotency, even against a buggy code path.

Retention is expressed as TTL indexes on the collections whose §6 retention is
a plain "drop after N". The `candles` tiering (Mongo → S3) is not a TTL — it is
the rollup job's responsibility (EM-58) because expired bars must be archived,
not deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from pymongo import ASCENDING

from emporos.persistence.collections import Collection

_DAY = timedelta(days=1)
_YEAR = timedelta(days=365)


@dataclass(frozen=True)
class IndexSpec:
    keys: tuple[tuple[str, int], ...]
    unique: bool = False
    expire_after: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("an index needs at least one key")
        if self.expire_after is not None and len(self.keys) != 1:
            raise ValueError("a TTL index must be on exactly one field")

    @classmethod
    def on(
        cls, *fields: str, unique: bool = False, expire_after: timedelta | None = None
    ) -> IndexSpec:
        return cls(tuple((name, ASCENDING) for name in fields), unique, expire_after)

    @property
    def name(self) -> str:
        return "_".join(f"{name}_{direction}" for name, direction in self.keys)

    @property
    def expire_after_seconds(self) -> int | None:
        return None if self.expire_after is None else int(self.expire_after.total_seconds())


@dataclass(frozen=True)
class TimeSeriesSpec:
    """A MongoDB time-series collection; retention is set at creation time."""

    time_field: str
    meta_field: str
    expire_after: timedelta
    granularity: str = "seconds"


@dataclass(frozen=True)
class CollectionSpec:
    name: Collection
    indexes: tuple[IndexSpec, ...] = ()
    timeseries: TimeSeriesSpec | None = None

    def __post_init__(self) -> None:
        names = [index.name for index in self.indexes]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate index declared on '{self.name}'")


@dataclass(frozen=True)
class Schema:
    collections: tuple[CollectionSpec, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [spec.name for spec in self.collections]
        if len(names) != len(set(names)):
            raise ValueError("a collection is declared twice")

    def spec_for(self, name: Collection) -> CollectionSpec:
        for spec in self.collections:
            if spec.name == name:
                return spec
        raise KeyError(name)


def _spec(
    name: Collection, *indexes: IndexSpec, timeseries: TimeSeriesSpec | None = None
) -> CollectionSpec:
    return CollectionSpec(name=name, indexes=indexes, timeseries=timeseries)


PLATFORM_SCHEMA = Schema(
    collections=(
        _spec(Collection.USERS),
        _spec(Collection.ACCOUNTS, IndexSpec.on("client_code", unique=True)),
        _spec(
            Collection.INSTRUMENTS,
            IndexSpec.on("exchange", "token", unique=True),
            IndexSpec.on("tradingsymbol", "exchange"),
            IndexSpec.on("name"),
        ),
        _spec(
            Collection.INSTRUMENT_VERSIONS,
            IndexSpec.on("token", "exchange", "valid_from"),
            IndexSpec.on("valid_to"),
        ),
        _spec(
            Collection.CANDLES,
            IndexSpec.on("instrument_id", "timeframe", "ts", unique=True),
            IndexSpec.on("timeframe", "ts"),
        ),
        _spec(
            Collection.TICKS,
            timeseries=TimeSeriesSpec(
                time_field="ts", meta_field="instrument_id", expire_after=7 * _DAY
            ),
        ),
        _spec(Collection.STRATEGIES, IndexSpec.on("name", unique=True)),
        _spec(
            Collection.STRATEGY_RUNS,
            IndexSpec.on("strategy_id", "session_date"),
            IndexSpec.on("created_at", expire_after=2 * _YEAR),
        ),
        _spec(
            Collection.SIGNALS,
            IndexSpec.on("strategy_run_id", "ts"),
            IndexSpec.on("instrument_id", "ts"),
            IndexSpec.on("ts", expire_after=_YEAR),
        ),
        _spec(
            Collection.ORDERS,
            IndexSpec.on("idempotency_key", unique=True),
            IndexSpec.on("ordertag", unique=True),
            IndexSpec.on("broker_order_id"),
            IndexSpec.on("state", "session_date"),
        ),
        _spec(Collection.ORDER_EVENTS, IndexSpec.on("order_id", "seq", unique=True)),
        _spec(
            Collection.EXECUTIONS,
            IndexSpec.on("broker_trade_id", unique=True),
            IndexSpec.on("order_id"),
        ),
        _spec(Collection.POSITIONS, IndexSpec.on("account_id", "instrument_id", unique=True)),
        _spec(
            Collection.PORTFOLIO_SNAPSHOTS,
            IndexSpec.on("account_id", "ts"),
            IndexSpec.on("ts", expire_after=5 * _YEAR),
        ),
        _spec(
            Collection.RISK_EVENTS,
            IndexSpec.on("rule", "ts"),
            IndexSpec.on("ts", expire_after=2 * _YEAR),
        ),
        _spec(
            Collection.RECONCILIATION_RUNS,
            IndexSpec.on("status"),
            IndexSpec.on("ts", expire_after=_YEAR),
        ),
        _spec(Collection.BACKTEST_RUNS, IndexSpec.on("strategy_id", "created_at")),
        _spec(Collection.BACKTEST_TRADES, IndexSpec.on("backtest_run_id")),
        _spec(
            Collection.SYSTEM_EVENTS,
            IndexSpec.on("correlation_id"),
            IndexSpec.on("type", "ts"),
            IndexSpec.on("ts", expire_after=30 * _DAY),
        ),
        _spec(Collection.MARKET_CALENDAR, IndexSpec.on("date", unique=True)),
        _spec(Collection.KILL_SWITCH),
        _spec(
            Collection.COMMANDS,
            IndexSpec.on("idempotency_key", unique=True),
            IndexSpec.on("status", "created_at"),
            IndexSpec.on("type", "created_at"),
            IndexSpec.on("created_at", expire_after=_YEAR),
        ),
        _spec(
            Collection.COMMAND_RESULTS,
            IndexSpec.on("command_id"),
            IndexSpec.on("created_at", expire_after=_YEAR),
        ),
    )
)

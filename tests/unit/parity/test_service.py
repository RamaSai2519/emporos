"""EM-185: a paper day is compared with its shadow backtest, stored, and rolled up — end to end
over a real backtest engine, with paper records that mirror it or deliberately degrade from it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from emporos.backtest.engine import BacktestEngine
from emporos.backtest.journal import BacktestEventSink
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.experiments import Verdict
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.parity import ParityKind, ParityReport
from emporos.marketdata.session import SessionWindow
from emporos.parity.inputs import PaperRunData
from emporos.parity.models import ParityStatus
from emporos.parity.service import ParityService
from emporos.parity.shadow import ShadowBacktest
from emporos.parity.verdict import ParityPolicy
from emporos.persistence.records import StrategyRunRecord
from emporos.strategies.snapshot import ConfigSnapshotter
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import (
    WORKED_DAY,
    BuyThenSell,
    FixedSchedule,
    FixedTicks,
    bars,
    config,
    registry,
)
from tests.support.fakes import RecordingAlertSink
from tests.support.parity import event, execution, order_record, risk_event, signal_record
from tests.support.strategies import T0
from tests.unit.parity.test_verdict import T as THRESHOLDS

RUN = "run-1"


class Engines:
    def __init__(self, candles: list) -> None:  # type: ignore[type-arg]
        self._reader = InMemoryCandles(candles)

    def build(self, session_date: date, sink: BacktestEventSink) -> BacktestEngine:
        return BacktestEngine(
            self._reader, registry(), FixedTicks(), lambda: FixedSchedule(), sink=sink
        )


class Source:
    def __init__(self) -> None:
        self.runs: dict[str, list[StrategyRunRecord]] = {}
        self.data: dict[str, PaperRunData] = {}
        self.loads = 0

    async def runs_on(self, session_date: str) -> tuple[StrategyRunRecord, ...]:
        return tuple(self.runs.get(session_date, ()))

    async def load(self, run: StrategyRunRecord) -> PaperRunData:
        self.loads += 1
        return self.data[run.id]


class Reports:
    def __init__(self) -> None:
        self.rows: list[ParityReport] = []

    async def append(self, report: ParityReport, report_id: str) -> bool:
        key = (report.strategy, report.behaviour_hash, report.period, report.kind)
        if any((r.strategy, r.behaviour_hash, r.period, r.kind) == key for r in self.rows):
            return False
        self.rows.append(report)
        return True

    async def dailies(self, strategy: str, behaviour_hash: str) -> list[ParityReport]:
        found = [
            r
            for r in self.rows
            if r.kind is ParityKind.DAILY and r.behaviour_hash == behaviour_hash
        ]
        return sorted(found, key=lambda r: r.first_session)

    async def daily_on(self, session_date: date) -> list[ParityReport]:
        return [
            r for r in self.rows if r.kind is ParityKind.DAILY and r.first_session == session_date
        ]


def paper_run(day: date, run_id: str = RUN, snapshot_edit: dict | None = None) -> StrategyRunRecord:  # type: ignore[type-arg]
    snapshot = ConfigSnapshotter().take(config(buy_at=2, sell_at=5))
    document = {**snapshot.document, **(snapshot_edit or {})}
    return StrategyRunRecord.model_validate(
        {
            "_id": run_id, "strategy_id": "s", "session_date": day.isoformat(), "created_at": T0,
            "config_snapshot": document, "config_hash": snapshot.content_hash,
        }
    )  # fmt: skip


def mirrored_paper(
    offset: int, filled: bool = True, sell: bool = True, buy_fill: str = "101.10"
) -> PaperRunData:
    """Paper records that say what the backtest of WORKED_DAY does: BUY 10 at the 2nd bar's close
    (101), filled at 101.10; SELL at the 5th (104), filled at 103.90."""
    d = timedelta(days=offset)
    buy_ts, sell_ts = T0 + d + timedelta(minutes=10), T0 + d + timedelta(minutes=25)
    signals = [signal_record("buy", buy_ts, "ENTRY", OrderSide.BUY, "101", sequence=1)]
    orders = [order_record("o-buy", "buy", created_at=buy_ts)]
    events = [event("o-buy", 1, buy_ts, "PENDING_NEW"), event("o-buy", 2, buy_ts, "OPEN")]
    fills = (
        [execution("t-buy", "o-buy", 10, buy_fill, buy_ts + timedelta(minutes=5), "5.97")]
        if filled
        else []
    )
    if sell:
        signals.append(signal_record("sell", sell_ts, "EXIT", OrderSide.SELL, "104", sequence=2))
        orders.append(order_record("o-sell", "sell", created_at=sell_ts, side=OrderSide.SELL))
        if filled:
            fills.append(
                execution(
                    "t-sell",
                    "o-sell",
                    10,
                    "103.90",
                    sell_ts + timedelta(minutes=5),
                    "6.20",
                    OrderSide.SELL,
                )
            )
    return PaperRunData(
        paper_run(T0.date() + d), tuple(signals), tuple(orders), tuple(events), tuple(fills), ()
    )


class Rig:
    def __init__(self, offset: int = 0) -> None:
        BuyThenSell.reset()
        self.offset = offset
        self.day = (T0 + timedelta(days=offset)).date()
        self.source, self.reports, self.alerts = Source(), Reports(), RecordingAlertSink()
        shadow = ShadowBacktest(
            Engines(bars(WORKED_DAY, day=offset)), registry(), Money.of("100000")
        )
        self.service = ParityService(
            self.source, shadow, self.reports, ParityPolicy.standard(THRESHOLDS),
            SessionWindow(), FixedClock(datetime(2026, 1, 9, 12, tzinfo=UTC)), IdGenerator(),
            self.alerts,
        )  # fmt: skip

    def paper(self, data: PaperRunData) -> Rig:
        self.source.runs[self.day.isoformat()] = [data.run]
        self.source.data[data.run.id] = data
        return self


async def test_a_paper_day_that_mirrors_the_backtest_matches_it_exactly() -> None:
    rig = Rig().paper(mirrored_paper(0))

    out = await rig.service.daily(rig.day)

    (daily,) = out.reports
    assert daily.kind is ParityKind.DAILY and daily.sessions == 1 and daily.matched_trades == 1
    assert daily.verdict is Verdict.INCONCLUSIVE  # one session is too few to judge, by design
    assert {g.outcome for g in daily.gates if g.name == "enough paper sessions"} == {"unknown"}
    deltas = daily.metrics["net_pnl"]
    assert deltas["absolute"] == "0.00" or deltas["absolute"] == "0"
    assert daily.metrics["fill_rate"]["absolute"] == "0"
    assert daily.payload["schema_version"] == 1
    assert rig.alerts.alerts == [] and not out.skipped


async def test_the_daily_is_rolled_up_into_a_cumulative_report() -> None:
    rig = Rig().paper(mirrored_paper(0))

    out = await rig.service.daily(rig.day)

    (cumulative,) = out.rolled_up
    assert cumulative.kind is ParityKind.CUMULATIVE and cumulative.sessions == 1
    assert [r.kind for r in rig.reports.rows] == [ParityKind.DAILY, ParityKind.CUMULATIVE]


async def test_the_last_session_of_the_week_also_writes_a_weekly_report() -> None:
    rig = Rig(offset=4).paper(mirrored_paper(4))  # Friday 2026-01-09

    out = await rig.service.daily(rig.day)

    assert {r.kind for r in out.rolled_up} == {ParityKind.CUMULATIVE, ParityKind.WEEKLY}


async def test_a_missed_fill_and_a_missing_signal_are_named_not_hidden() -> None:
    rig = Rig().paper(mirrored_paper(0, filled=False, sell=False))

    (daily,) = (await rig.service.daily(rig.day)).reports

    session = rig.service._codec.from_document(daily.payload)
    assert [r.status for r in session.signals] == [
        ParityStatus.MISSED,
        ParityStatus.BACKTEST_ONLY_SIGNAL,
    ]
    assert daily.metrics["fill_rate"]["paper"] == "0"
    assert daily.metrics["fill_rate"]["absolute"] == "-1"
    assert daily.matched_trades == 0


async def test_a_risk_rejected_signal_is_named() -> None:
    data = mirrored_paper(0, filled=False, sell=False)
    rejected = PaperRunData(data.run, data.signals, (), (), (), (risk_event("buy"),))
    rig = Rig().paper(rejected)

    (daily,) = (await rig.service.daily(rig.day)).reports

    statuses = [r.status for r in rig.service._codec.from_document(daily.payload).signals]
    assert statuses[0] is ParityStatus.RISK_REJECTED


async def test_a_day_already_reported_is_not_run_or_written_again() -> None:
    rig = Rig().paper(mirrored_paper(0))
    await rig.service.daily(rig.day)
    loads, rows = rig.source.loads, len(rig.reports.rows)

    again = await rig.service.daily(rig.day)

    assert again.already_reported == 1 and not again.reports
    assert rig.source.loads == loads and len(rig.reports.rows) == rows


async def test_a_run_whose_config_cannot_be_reproduced_is_skipped_with_its_reason() -> None:
    rig = Rig()
    tampered = paper_run(
        rig.day,
        snapshot_edit={
            "execution": {"limit_buffer_bps": "9", "reprice_after_seconds": 30, "max_reprices": 3}
        },
    )
    rig.paper(PaperRunData(tampered))

    out = await rig.service.daily(rig.day)

    assert not out.reports and [s.run_id for s in out.skipped] == [RUN]
    assert "hash" in out.skipped[0].reason
    assert rig.reports.rows == []


async def test_a_rejected_verdict_raises_an_alert() -> None:
    rig = Rig()
    rig.service._policy = ParityPolicy.standard(
        THRESHOLDS.model_copy(update={"min_sessions": 1, "min_matched_trades": 0 + 1})
    )
    rig.paper(mirrored_paper(0, buy_fill="103.50"))  # paper pays far above what the backtest did

    await rig.service.daily(rig.day)

    assert any(name.startswith("parity_degraded:") for name, _ in rig.alerts.alerts)

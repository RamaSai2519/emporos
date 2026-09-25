"""The replay engine end to end on hand-built bars, a scripted decider and a flat cost double."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from emporos.eventtrader.events import MarketContext, MarketEvent
from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.replay.book import Book
from emporos.eventtrader.replay.costs import FlatCosts
from emporos.eventtrader.replay.engine import FixedPosture, ReplayEngine, RunResult
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.records import Scenario, TradeRecord
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.risk.limits import RiskLimits
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.eventtrader.stages.stages import EventInput
from emporos.research.scans.base import ScanExecution
from tests.unit.eventtrader.fakes import NOON, event
from tests.unit.eventtrader.replay.fakes import (
    MON,
    TUE,
    FakeMarket,
    at,
    daily,
    flat_day,
    replace_bar,
)

D = Decimal
X = "NSE:2885"
Y = "NSE:1594"
EXEC = ScanExecution(position_value=D(50000))
COSTS = FlatCosts(D("0.001"), D("0.002"))


def plan(
    instrument: Instrument = Instrument.CASH_INTRADAY, side: Side = Side.LONG, stop: float = 3.0,
    target: float = 6.0, hold_days: int = 0,
) -> TradePlan:  # fmt: skip
    return TradePlan(instrument, side, stop, target, hold_days, 75)


def trade(p: TradePlan) -> PipelineDecision:
    return PipelineDecision("E", Verdict.TRADE, p)


class ScriptedDecider:
    def __init__(self, *answers: PipelineDecision) -> None:
        self._answers = list(answers)
        self.seen: list[EventInput] = []

    async def decide(self, item: EventInput) -> PipelineDecision:
        self.seen.append(item)
        return self._answers.pop(0)


class NoContext:
    def context(self, event: MarketEvent, decision_at: datetime) -> MarketContext:
        return MarketContext({})


class Meter:
    """Each call the decider makes costs Rs 2: the decider bumps it."""

    def __init__(self) -> None:
        self.spent = D(0)

    def total_inr(self) -> Decimal:
        return self.spent


class MeteredDecider(ScriptedDecider):
    def __init__(self, meter: Meter, *answers: PipelineDecision) -> None:
        super().__init__(*answers)
        self._meter = meter

    async def decide(self, item: EventInput) -> PipelineDecision:
        self._meter.spent += D(2)
        return await super().decide(item)


class StubOptions:
    def __init__(self, answer: TradeRecord | str) -> None:
        self._answer = answer
        self.calls = 0

    def place(
        self, event: MarketEvent, plan: TradePlan, decision_at: datetime, book: Book, scale: Decimal
    ) -> TradeRecord | str:
        self.calls += 1
        return self._answer


def market() -> FakeMarket:
    m = FakeMarket([MON, TUE, date(2024, 3, 6), date(2024, 3, 7), date(2024, 3, 8)])
    for name in (X, Y):
        for day in m.sessions:
            m.put_day(name, day, flat_day(name, day))
    return m


def engine(
    decider: ScriptedDecider, m: FakeMarket | None = None, limits: RiskLimits | None = None,
    **kw: object,
) -> ReplayEngine:  # fmt: skip
    return ReplayEngine(
        decider, NoContext(), m or market(), RiskEngine(limits), COSTS, EXEC,
        **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def ev(eid: str = "E1", name: str = X, at_: datetime = NOON, **kw: object) -> MarketEvent:
    when = at_ - timedelta(minutes=12)
    return event(event_id=eid, instrument_id=name, published_at=when, usable_from=when, **kw)


async def run(eng: ReplayEngine, *events: MarketEvent) -> RunResult:
    return await eng.run(list(events))


async def test_an_intraday_long_hits_its_target() -> None:
    m = market()
    m.put_day(X, MON, replace_bar(flat_day(X, MON), 12, 5, o=100, h=106.5, low=100, c=106))
    result = await run(engine(ScriptedDecider(trade(plan())), m), ev())

    [t] = result.trades
    assert (t.side, t.product, t.exit_reason) == (Side.LONG, Product.INTRADAY, ExitReason.TARGET)
    assert t.quantity == 500  # the Rs 50,000 position cap binds before the Rs 2,000 risk
    assert (t.entry_price, t.exit_price, t.stop_price) == (D(100), D(106), D(97))
    assert t.gross_pnl == D(3000)
    assert t.cost_benchmark == D(50) and t.cost_adverse == D(100)
    assert result.net(Scenario.BENCHMARK) == D(2950)
    assert result.stats.verdicts["trade"] == 1


async def test_the_decision_is_taken_two_minutes_after_the_event_is_usable() -> None:
    decider = ScriptedDecider(trade(plan()))
    await run(engine(decider), ev())

    assert decider.seen[0].decision_at == at(MON, 11, 50)


async def test_a_short_stopped_out_loses_and_is_recorded_as_a_stop() -> None:
    m = market()
    m.put_day(X, MON, replace_bar(flat_day(X, MON), 12, 5, o=100, h=104, low=99.5, c=103))
    result = await run(engine(ScriptedDecider(trade(plan(side=Side.SHORT))), m), ev())

    [t] = result.trades
    assert (t.exit_reason, t.exit_price, t.gross_pnl) == (ExitReason.STOP, D(103), D(-1500))


async def test_a_quiet_intraday_trade_is_squared_off() -> None:
    result = await run(engine(ScriptedDecider(trade(plan()))), ev())

    [t] = result.trades
    assert t.exit_reason is ExitReason.SQUARE_OFF and t.exit_ts == at(MON, 15, 15)


async def test_verdicts_other_than_trade_never_reach_the_market() -> None:
    no = PipelineDecision("E", Verdict.JUDGE_NO)
    result = await run(engine(ScriptedDecider(no, no)), ev("E1"), ev("E2"))

    assert result.trades == ()
    assert result.stats.verdicts == {"judge_no": 2}
    assert result.stats.events == 2


async def test_market_wide_events_are_skipped_before_any_call() -> None:
    decider = ScriptedDecider()
    result = await run(engine(decider), ev(name=""))

    assert decider.seen == [] and result.stats.skipped["market_wide"] == 1


async def test_the_risk_engine_can_refuse_and_the_rule_is_counted() -> None:
    result = await run(engine(ScriptedDecider(trade(plan(stop=8.0)))), ev())

    assert result.trades == () and result.stats.refused["stop_width"] == 1


async def test_a_hold_posture_refuses_every_entry() -> None:
    eng = engine(ScriptedDecider(trade(plan())), posture=FixedPosture(D(0)))

    assert (await run(eng, ev())).stats.refused["posture_hold"] == 1


async def test_an_intraday_decision_after_the_entry_window_is_refused() -> None:
    late = at(MON, 15, 2).astimezone(NOON.tzinfo)
    result = await run(engine(ScriptedDecider(trade(plan()))), ev(at_=late))

    assert result.trades == () and result.stats.refused["entry_window"] == 1


async def test_an_order_the_bar_cannot_absorb_is_counted_as_no_entry() -> None:
    m = market()
    m.put_day(X, MON, replace_bar(flat_day(X, MON), 11, 50, volume=100))  # 10 shares of room
    result = await run(engine(ScriptedDecider(trade(plan())), m), ev())

    assert result.trades == () and result.stats.no_entry["no_volume"] == 1


async def test_a_swing_decided_after_hours_enters_the_next_session_at_0920() -> None:
    after = at(MON, 16, 30).astimezone(NOON.tzinfo)
    m = market()
    for day in m.sessions[1:]:
        m.put_daily(X, daily(day, 100, 101, 99, 100.5))
    swing = plan(Instrument.CASH_SWING, hold_days=2)
    result = await run(engine(ScriptedDecider(trade(swing)), m), ev(at_=after))

    [t] = result.trades
    assert t.entry_ts == at(TUE, 9, 20) and t.exit_reason is ExitReason.TIME
    assert t.quantity == 250  # the Rs 25,000 swing position cap
    assert t.exit_price == D("100.5")


async def test_token_cost_is_attributed_to_the_events_that_spent_it() -> None:
    meter = Meter()
    no = PipelineDecision("E", Verdict.NOT_MATERIAL)
    decider = MeteredDecider(meter, no, trade(plan()), no)
    third = ev("E3", at_=NOON + timedelta(hours=1))
    result = await run(engine(decider, tokens=meter), ev("E1"), ev("E2", name=Y), third)

    [t] = result.trades
    assert t.event_id == "E2" and t.token_cost_inr == D(2)
    assert result.token_cost_inr == D(6)  # every call, trades or not
    assert result.net(Scenario.BENCHMARK) == t.net_benchmark - D(6)


async def test_the_total_loss_kill_stops_the_track() -> None:
    m = market()
    m.put_day(X, MON, replace_bar(flat_day(X, MON), 12, 5, o=100, h=100, low=96, c=97))
    decider = ScriptedDecider(trade(plan()), trade(plan()), trade(plan()))
    later = NOON + timedelta(hours=1)
    eng = engine(decider, m, RiskLimits(total_loss=D(1000)))
    result = await run(eng, ev("E1"), ev("E2", name=Y, at_=later), ev("E3", at_=later))

    assert [t.exit_reason for t in result.trades] == [ExitReason.STOP]  # Rs 1,550 lost by 12:10
    assert result.stats.killed_at == at(MON, 12, 50)
    assert result.stats.skipped["track_stopped"] == 2
    assert len(decider.seen) == 1  # nothing decided once the kill tripped


async def test_the_kill_closes_a_position_still_open_at_its_last_mark() -> None:
    m = market()
    day = flat_day(X, MON)
    for minute in range(12 * 60 + 20, 15 * 60 + 25, 5):  # X slides to 96 and stays, above its stop
        day = replace_bar(day, minute // 60, minute % 60, o=96, h=96, low=96, c=96)
    m.put_day(X, MON, day)
    limits = RiskLimits(total_loss=D(1000), daily_loss=D(100000), open_risk=D(100000))
    decider = ScriptedDecider(trade(plan(stop=5.0, target=20.0)), trade(plan()))
    later = NOON + timedelta(minutes=45)
    result = await run(engine(decider, m, limits), ev("E1"), ev("E2", name=Y, at_=later))

    [t] = result.trades
    assert t.exit_reason is ExitReason.KILL and t.exit_price == D(96)
    assert t.exit_ts == at(MON, 12, 35) and t.gross_pnl == -4 * t.quantity
    assert result.stats.killed_at == at(MON, 12, 35)


async def test_options_need_a_placer_and_report_why_they_were_not_placed() -> None:
    call = plan(Instrument.CALL, hold_days=3)
    none = await run(engine(ScriptedDecider(trade(call))), ev())
    stub = StubOptions("premium_over_budget")
    refused = await run(engine(ScriptedDecider(trade(call)), options=stub), ev())

    assert none.stats.refused["options_unavailable"] == 1
    assert refused.stats.refused["premium_over_budget"] == 1 and stub.calls == 1


async def test_a_placed_option_joins_the_book_carrying_its_token_cost() -> None:
    meter = Meter()
    option = TradeRecord(
        "E1", X, "RELIANCE", Instrument.CALL, Product.OPTION, Side.LONG, 250, at(MON, 12), D(20),
        at(TUE, 15), D(30), ExitReason.TIME, None, D(5000), D(2500), D(40), D(80),
    )  # fmt: skip
    eng = engine(MeteredDecider(meter, trade(plan(Instrument.CALL))), tokens=meter,
                 options=StubOptions(option))  # fmt: skip
    result = await run(eng, ev())

    [t] = result.trades
    assert t.token_cost_inr == D(2) and t.gross_pnl == D(2500)

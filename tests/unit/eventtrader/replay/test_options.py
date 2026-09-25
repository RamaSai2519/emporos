"""Buying a call or put from end-of-day chains: contract choice, refusals by reason, exits."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from emporos.eventtrader.pipeline import TradePlan
from emporos.eventtrader.replay.book import Book
from emporos.eventtrader.replay.costs import FlatCosts
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.options import ChainPlacer, NoChains
from emporos.eventtrader.replay.records import TradeRecord
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote, OptionRight
from tests.unit.eventtrader.fakes import event
from tests.unit.eventtrader.replay.fakes import (
    FRI,
    MON,
    THU,
    TUE,
    WED,
    FakeMarket,
    at,
    daily,
    flat_day,
)

D = Decimal
NEAR, FAR = date(2024, 3, 14), date(2024, 3, 28)  # 10 and 24 days from Monday 4 March
LOT = 250
CALL, PUT = OptionRight.CALL, OptionRight.PUT
COSTS = FlatCosts(D("0.001"), D("0.002"))


def quote(strike: int, right: OptionRight, close: str, traded: int = 100) -> OptionQuote:
    return OptionQuote(D(strike), right, D(close), D(close), 1000, traded)


def chain(
    day: date, closes: dict[tuple[int, OptionRight], str] | None = None, level: str = "1000"
) -> ChainSnapshot:
    """Strikes 960..1040 in steps of 20; premiums by (strike, right), default 12."""
    given = closes or {}
    quotes = {
        (D(k), r): quote(k, r, given.get((k, r), "12"))
        for k in range(960, 1041, 20)
        for r in (CALL, PUT)
    }
    far, near = ExpiryChain(FAR, quotes), ExpiryChain(NEAR, quotes)
    return ChainSnapshot(day, "RELIANCE", D(level), LOT, D(20), D("0.05"), {NEAR: near, FAR: far})


class Chains:
    def __init__(self, by_day: dict[date, ChainSnapshot]) -> None:
        self._by_day = by_day

    def snapshot(self, symbol: str, day: date) -> ChainSnapshot | None:
        return self._by_day.get(day) if symbol == "RELIANCE" else None


def market() -> FakeMarket:
    m = FakeMarket([MON, TUE, WED, THU, FRI])
    for d in (TUE, WED, THU, FRI):
        m.put_daily("NSE:2885", daily(d, 1000, 1010, 990, 1000))
    return m


def placer(chains: object, m: FakeMarket | None = None, **limits: object) -> ChainPlacer:
    return ChainPlacer(chains, m or market(), RiskEngine(), COSTS)  # type: ignore[arg-type]


def go(
    p: ChainPlacer,
    instrument: Instrument = Instrument.CALL,
    hold: int = 3,
    at_: datetime | None = None,
    scale: str = "1",
    book: Book | None = None,
) -> TradeRecord | str:
    plan = TradePlan(instrument, Side.LONG, 3.0, 6.0, hold, 75)
    return p.place(
        event(), plan, at_ or at(MON, 11, 50), book or Book(D(100000), market()), D(scale)
    )


def days() -> dict[date, ChainSnapshot]:
    return {d: chain(d) for d in (MON, TUE, WED, THU, FRI)}


def test_a_call_walks_out_of_the_money_to_the_first_contract_within_budget() -> None:
    # at the money 1000 (Rs 30 x 250 = 7,500) and 1020 (Rs 22 = 5,500) are too dear; 1040 fits
    closes = {(1000, CALL): "30", (1020, CALL): "22", (1040, CALL): "14"}
    table = days()
    table[MON] = chain(MON, closes)

    out = go(placer(Chains(table)))

    assert isinstance(out, TradeRecord)
    assert out.name.endswith(":2024-03-28:1040CE") and out.entry_price == D(14)
    assert out.quantity == LOT and out.product is Product.OPTION and out.side is Side.LONG
    assert out.entry_ts == at(MON, 15, 30)


def test_a_put_walks_down() -> None:
    closes = {(1000, PUT): "30", (980, PUT): "22", (960, PUT): "14"}
    table = days()
    table[MON] = chain(MON, closes)

    out = go(placer(Chains(table)), Instrument.PUT)

    assert isinstance(out, TradeRecord) and out.name.endswith(":2024-03-28:960PE")


def test_no_contract_within_two_strikes_is_premium_over_budget() -> None:
    closes = {(1000, CALL): "30", (1020, CALL): "22", (1040, CALL): "21"}
    table = days()
    table[MON] = chain(MON, closes)

    assert go(placer(Chains(table))) == "premium_over_budget"


def test_a_walk_that_finds_nothing_traded_says_so() -> None:
    quotes = {(D(k), CALL): quote(k, CALL, "12", traded=0) for k in range(960, 1041, 20)}
    snap = ChainSnapshot(
        MON, "RELIANCE", D(1000), LOT, D(20), D("0.05"), {FAR: ExpiryChain(FAR, quotes)}
    )

    assert go(placer(Chains({MON: snap}))) == "no_traded_contract"


def test_only_the_near_expiry_listed_is_no_expiry() -> None:
    quotes = {(D(1000), CALL): quote(1000, CALL, "12")}
    snap = ChainSnapshot(
        MON, "RELIANCE", D(1000), LOT, D(20), D("0.05"), {NEAR: ExpiryChain(NEAR, quotes)}
    )

    assert go(placer(Chains({MON: snap}))) == "no_expiry"


def test_a_name_without_chains_is_refused_as_no_chain() -> None:
    assert go(placer(NoChains())) == "no_chain"


def test_a_hold_posture_is_refused() -> None:
    assert go(placer(Chains(days())), scale="0") == "posture_hold"


def test_a_decision_after_the_last_session_has_no_later_session() -> None:
    late = at(FRI, 16, 30)

    assert go(placer(Chains(days())), at_=late) == "no_later_session"


def test_the_risk_engine_reviews_an_option_like_any_entry() -> None:
    m = market()
    book = Book(D(100000), m)
    for i in range(4):  # the four swing slots are taken
        book.add(
            TradeRecord(
                f"E{i}", f"NSE:{i}", "X", Instrument.CASH_SWING, Product.SWING, Side.LONG, 10,
                at(MON, 10), D(100), at(FRI, 15), D(100), ExitReason.TIME, D(97), D(30), D(0),
                D(0), D(0),
            )
        )  # fmt: skip

    assert go(placer(Chains(days()), m), book=book) == "max_positions"


def test_the_exit_is_the_close_of_the_session_the_underlying_reaches_its_target() -> None:
    m = market()
    m.put_daily("NSE:2885", daily(WED, 1000, 1065, 995, 1050))  # +6% reached on Wednesday
    table = days()
    table[WED] = chain(WED, {(1000, CALL): "35", (1020, CALL): "26", (1040, CALL): "20"})

    out = go(placer(Chains(table), m))

    assert isinstance(out, TradeRecord)
    assert out.exit_reason is ExitReason.TARGET and out.exit_ts == at(WED, 15, 30)
    assert out.entry_price == D(12) and out.exit_price == D(35)  # the 1000 call, marked Wednesday
    assert out.gross_pnl == (D(35) - D(12)) * LOT


def test_a_put_exits_when_the_underlying_falls_to_its_target() -> None:
    m = market()
    m.put_daily("NSE:2885", daily(TUE, 1000, 1005, 935, 940))  # -6% on Tuesday

    out = go(placer(Chains(days()), m), Instrument.PUT)

    assert isinstance(out, TradeRecord) and out.exit_reason is ExitReason.TARGET
    assert out.exit_ts == at(TUE, 15, 30)


def test_without_a_touch_the_exit_is_after_the_hold_days() -> None:
    out = go(placer(Chains(days())), hold=2)

    assert isinstance(out, TradeRecord)
    assert out.exit_reason is ExitReason.TIME and out.exit_ts == at(WED, 15, 30)


def test_data_that_ends_before_the_hold_is_over_is_end_of_data() -> None:
    m = FakeMarket([MON, TUE])
    m.put_daily("NSE:2885", daily(TUE, 1000, 1010, 990, 1000))

    out = go(placer(Chains(days()), m), hold=5)

    assert isinstance(out, TradeRecord) and out.exit_reason is ExitReason.END_OF_DATA


def test_a_missing_exit_quote_falls_back_to_the_last_earlier_mark() -> None:
    table = days()
    table[WED] = ChainSnapshot(
        WED, "RELIANCE", D(1000), LOT, D(20), D("0.05"), {FAR: ExpiryChain(FAR, {})}
    )
    table[TUE] = chain(TUE, {(1040, CALL): "13"})
    table[MON] = chain(MON, {(1000, CALL): "30", (1020, CALL): "22", (1040, CALL): "14"})

    out = go(placer(Chains(table)), hold=2)

    assert isinstance(out, TradeRecord) and out.exit_ts == at(WED, 15, 30)
    assert out.exit_price == D(13)  # Tuesday's mark, the last session that quoted it


def test_an_after_hours_event_is_entered_at_the_next_sessions_close() -> None:
    out = go(placer(Chains(days())), at_=at(MON, 17, 0))

    assert isinstance(out, TradeRecord) and out.entry_ts == at(TUE, 15, 30)
    assert timedelta(0) < out.exit_ts - out.entry_ts


def test_stop_and_target_are_levels_on_the_bars_series_not_the_chains_own_level() -> None:
    # after a 1:1 bonus the bars series is half the level the day's chain quotes
    m = market()
    m.put_day("NSE:2885", MON, flat_day("NSE:2885", MON, 500))
    for d in (TUE, WED, THU, FRI):
        m.put_daily("NSE:2885", daily(d, 500, 505, 495, 500))
    m.put_daily("NSE:2885", daily(WED, 500, 535, 498, 530))  # +6% on the bars series

    out = go(placer(Chains(days()), m))

    assert isinstance(out, TradeRecord)
    assert out.exit_reason is ExitReason.TARGET and out.exit_ts == at(WED, 15, 30)

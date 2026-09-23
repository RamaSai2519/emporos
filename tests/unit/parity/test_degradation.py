"""EM-185: the degradation formulas, and the identity property that proves the plumbing unbiased."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from emporos.backtest.journal import RecordingSink
from emporos.backtest.portfolio import TradeDirection
from emporos.domain.money import Money
from emporos.parity.ledger import SessionParity, SignalParity, TradeFacts, TradePair
from emporos.parity.metrics import Delta, ParityAnalyzer, ParityMetric
from emporos.parity.models import ParityStatus, ReferenceKind, SideOutcome, Slippage
from emporos.parity.outcomes import BacktestOutcomes, BacktestSignals
from emporos.parity.session import SessionKey, SessionParityBuilder, SideInputs
from emporos.parity.trades import PaperTrades, TradePairer
from tests.support.backtest_engine import WORKED_DAY, bars, config, run
from tests.support.parity import execution, paper_data
from tests.support.strategies import INSTRUMENT, T0

D = Decimal


def facts(net: str, fees: str = "0", notional: str = "1000", at: int = 0) -> TradeFacts:
    return TradeFacts(
        T0 + timedelta(minutes=at), T0 + timedelta(minutes=at + 1), 10, D(notional),
        D(net) + D(fees), D(fees),
    )  # fmt: skip


def outcome(
    filled: int = 10, price: str = "100", charges: str = "5", bps: str | None = None
) -> SideOutcome:
    return SideOutcome(
        10,
        filled,
        Money.of(price) if filled else None,
        Money.of(charges),
        slippage=None if bps is None else Slippage(D(bps), Money.of(price), ReferenceKind.MID),
    )


def row(
    paper: SideOutcome | None, backtest: SideOutcome | None, symbol: str = INSTRUMENT
) -> SignalParity:
    return SignalParity(symbol, ParityStatus.MATCHED, "", None, None, paper, backtest)


def session(
    rows: list[SignalParity], trades: list[TradePair], day: int = 5, strategy: str = "s"
) -> SessionParity:
    return SessionParity(
        strategy, "run", date(2026, 1, day), "h", D(50000), tuple(rows), tuple(trades)
    )


class TestDelta:
    def test_absolute_is_paper_minus_backtest_and_relative_uses_the_backtest_magnitude(
        self,
    ) -> None:
        delta = Delta(D("-10"), D("-15"))
        assert delta.absolute == D("-5") and delta.relative == D("-0.5")

    def test_missing_or_zero_bases_give_none(self) -> None:
        assert Delta(None, D(1)).absolute is None
        assert Delta(D(0), D(1)).relative is None


class TestFormulas:
    def analyse(self) -> ParityAnalyzer:
        return ParityAnalyzer()

    def test_fill_rate_slippage_cost_and_turnover(self) -> None:
        rows = [
            row(outcome(10, "100", "5", "10"), outcome(10, "100", "5", "2")),
            row(outcome(0), outcome(10, "100", "5", "4")),  # paper's order never filled
        ]
        m = self.analyse().metrics([session(rows, [])])
        assert (m.paper.fill_rate, m.backtest.fill_rate) == (D("0.5"), D(1))
        assert m.delta(ParityMetric.FILL_RATE).absolute == D("-0.5")
        assert (m.paper.slippage_mean_bps, m.backtest.slippage_mean_bps) == (D(10), D(3))
        assert m.delta(ParityMetric.TURNOVER).absolute == D(-1000)
        assert m.turnover_ratio == D("0.5")
        assert m.paper.cost_bps == D(10) / D(1000) * D(10000)

    def test_p90_slippage_is_nearest_rank(self) -> None:
        rows = [row(outcome(bps=str(n)), None) for n in range(1, 11)]
        totals = self.analyse().metrics([session(rows, [])]).paper
        assert totals.slippage_p90_bps == D(9)

    def test_expectancy_win_rate_pnl_and_drawdown_come_from_the_trips(self) -> None:
        trips = [
            TradePair(
                INSTRUMENT, TradeDirection.LONG, facts("20", "2", at=0), facts("30", "2", at=0)
            ),
            TradePair(
                INSTRUMENT, TradeDirection.LONG, facts("-50", "2", at=10), facts("10", "2", at=10)
            ),
        ]
        m = self.analyse().metrics([session([], trips)])
        assert (m.paper.trades, m.paper.wins, m.paper.win_rate) == (2, 1, D("0.5"))
        assert m.paper.net_pnl == D(-30) and m.backtest.net_pnl == D(40)
        assert m.paper.expectancy == (D("0.02") + D("-0.05")) / 2
        assert abs(m.paper.worst_drawdown - D(50) / D(50020)) < D("1e-20")
        assert m.backtest.worst_drawdown == D(0)
        assert m.matched_trades == 2

    def test_a_side_with_no_data_reports_none_not_zero(self) -> None:
        m = self.analyse().metrics([session([], [])])
        assert (
            m.paper.fill_rate is None and m.paper.expectancy is None and m.signal_agreement is None
        )
        assert m.delta(ParityMetric.FILL_RATE).absolute is None

    def test_signal_agreement_counts_one_sided_rows_as_disagreement(self) -> None:
        both = SignalParity(INSTRUMENT, ParityStatus.MATCHED, "", _pt(), _pt(), None, None)
        lonely = SignalParity(
            INSTRUMENT, ParityStatus.PAPER_ONLY_SIGNAL, "", _pt(), None, None, None
        )
        m = self.analyse().metrics([session([both, both, both, lonely], [])])
        assert m.signal_agreement == D("0.75") and m.matched_signals == 3

    def test_weekly_ratios_are_ratios_of_sums_not_means_of_ratios(self) -> None:
        day1 = session([row(outcome(10), outcome(10))], [], day=5)
        day2 = session([row(outcome(0), outcome(10)) for _ in range(3)], [], day=6)
        assert self.analyse().metrics([day1, day2]).paper.fill_rate == D("0.25")

    def test_slices_cover_strategy_symbol_session_and_trade(self) -> None:
        a = session(
            [row(outcome(), outcome(), "NSE:1")],
            [TradePair("NSE:1", TradeDirection.LONG, facts("5"), facts("5"))],
            5,
            "alpha",
        )
        b = session([row(outcome(0), outcome(), "NSE:2")], [], 6, "beta")
        sliced = self.analyse().slices([a, b])
        assert set(sliced.by_strategy) == {"alpha", "beta"}
        assert set(sliced.by_symbol) == {"NSE:1", "NSE:2"}
        assert set(sliced.by_session) == {date(2026, 1, 5), date(2026, 1, 6)}
        assert sliced.by_symbol["NSE:2"].paper.fill_rate == D(0)
        assert sliced.by_symbol["NSE:1"].paper.trades == 1
        assert len(sliced.by_trade) == 1 and sliced.overall.sessions == 2


def _pt():  # type: ignore[no-untyped-def]
    from tests.unit.parity.test_matcher import paper

    return paper()


KEY = SessionKey("buy_then_sell", "run-1", T0.date(), "hash", D(100000))


async def _journal() -> tuple[RecordingSink, tuple]:  # type: ignore[type-arg]
    sink = RecordingSink()
    result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5), sink=sink)
    return sink, result.trades


class TestIdentity:
    """Give the analyzer the backtest's own events as both sides: every signal MATCHED, every
    trip paired, every delta zero. One test that proves nothing in the plumbing is biased."""

    async def test_a_backtest_compared_with_itself_has_no_degradation(self) -> None:
        sink, trades = await _journal()
        side = SideInputs(BacktestSignals().points(sink), BacktestOutcomes().by_ref(sink), trades)

        parity = SessionParityBuilder().build(KEY, side, side)
        m = ParityAnalyzer().metrics([parity])

        assert [r.status for r in parity.signals] == [ParityStatus.MATCHED] * 2
        assert m.signal_agreement == 1 and m.matched_trades == len(trades) == 1
        assert m.paper == m.backtest
        for metric, delta in m.all().items():
            assert delta.absolute in (None, D(0)), metric
        assert m.paper.fill_rate == 1 and m.paper.trades == 1

    async def test_paper_round_trips_equal_the_backtests_for_identical_fills(self) -> None:
        sink, trades = await _journal()
        executions = tuple(
            execution(
                f"t{f.sequence}",
                f.fill.order_id,
                f.fill.quantity,
                str(f.fill.price.amount),
                f.fill.ts,
                str(f.charges.total.amount),
                f.fill.side,
            )
            for f in sink.fills
        )
        built = PaperTrades().build(paper_data(executions=executions), Money.of("100000"))

        assert [replace(t, strategy_run_id="") for t in built] == [
            replace(t, strategy_run_id="") for t in trades
        ]

    def test_unpaired_trips_are_kept(self) -> None:
        from emporos.backtest.portfolio import ClosedTrade

        def trip(at: int, instrument: str = INSTRUMENT) -> ClosedTrade:
            return ClosedTrade(
                instrument, TradeDirection.LONG, 10, T0 + timedelta(minutes=at),
                T0 + timedelta(minutes=at + 1), Money.of("100"), Money.of("101"),
                Money.of("10"), Money.of("1"), "r",
            )  # fmt: skip

        pairs = TradePairer().pair([trip(0), trip(10)], [trip(0)])
        assert [(p.paper is not None, p.backtest is not None) for p in pairs] == [
            (True, True),
            (True, False),
        ]

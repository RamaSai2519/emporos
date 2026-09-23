"""EM-185: outcomes are derived per signal on both sides, and classified into one status."""

from __future__ import annotations

from datetime import timedelta

from emporos.backtest.journal import RecordingSink
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.parity.models import ParityStatus, SideOutcome
from emporos.parity.outcomes import (
    BacktestOutcomes,
    BacktestSignals,
    PaperOutcomes,
    PaperSignals,
    StatusClassifier,
)
from tests.support.parity import (
    event,
    execution,
    order_record,
    paper_data,
    risk_event,
    signal_record,
)
from tests.support.strategies import T0

S = timedelta(seconds=1)


def outcome(ordered: int = 10, filled: int = 10, rejected: bool = False) -> SideOutcome:
    return SideOutcome(ordered, filled, Money.of("100") if filled else None, Money.zero(), rejected)


class TestPaperOutcomes:
    def test_a_filled_signal_reports_quantity_average_charges_and_slippage(self) -> None:
        data = paper_data(
            (signal_record(),),
            (order_record(),),
            (event("ord-1", 1, T0, "PENDING_NEW"), event("ord-1", 2, T0 + S, "OPEN")),
            (execution("t1", quantity=4, price="100"), execution("t2", quantity=6, price="101")),
        )
        result = PaperOutcomes().by_signal(data)["sig-1"]
        assert (result.ordered_quantity, result.filled_quantity) == (10, 10)
        assert result.average_fill_price == Money.of("100.6")
        assert result.charges == Money.of("10")
        assert result.slippage is not None and result.slippage.bps == 60
        assert result.latency is not None and result.latency.placement == S

    def test_reprice_children_do_not_double_the_ordered_quantity(self) -> None:
        root = order_record("o1", state="CANCELLED")
        child = order_record("o2", parent="o1", quantity=6, created_at=T0 + 30 * S)
        data = paper_data(
            (signal_record(),), (root, child), (), (execution("t", order_id="o2", quantity=6),)
        )
        result = PaperOutcomes().by_signal(data)["sig-1"]
        assert (result.ordered_quantity, result.filled_quantity) == (10, 6)

    def test_a_risk_event_marks_the_signal_rejected(self) -> None:
        data = paper_data((signal_record(),), (), (), (), (risk_event(),))
        result = PaperOutcomes().by_signal(data)["sig-1"]
        assert result.rejected_by_risk and result.ordered_quantity == 0 and result.latency is None

    def test_the_recorded_decision_quote_is_the_slippage_reference(self) -> None:
        signal = signal_record(
            quote_bid=Money.of("99"), quote_ask=Money.of("101"), quote_ts=T0, quote_source="q"
        )
        data = paper_data((signal,), (order_record(),), (), (execution("t", price="100"),))
        result = PaperOutcomes().by_signal(data)["sig-1"]
        assert result.slippage is not None and result.slippage.kind.value == "mid"


class TestSignalPoints:
    def test_rows_that_are_not_strategy_signals_are_skipped(self) -> None:
        bare = signal_record("bare").model_copy(update={"kind": None})
        points = PaperSignals().points(paper_data((bare, signal_record("real", sequence=2))))
        assert [p.ref for p in points] == ["real"]

    def test_backtest_points_carry_the_journal_sequence(self) -> None:
        sink = RecordingSink()
        from tests.support.strategies import make_signal

        sink.on_signal(make_signal(), system=True)
        (point,) = BacktestSignals().points(sink)
        assert (point.ref, point.sequence, point.system) == ("bt-1", 1, True)
        assert BacktestOutcomes().by_ref(sink)["bt-1"].ordered_quantity == 0


class TestClassifier:
    def status(self, paper: SideOutcome | None, backtest: SideOutcome | None) -> ParityStatus:
        return StatusClassifier().classify(paper, backtest)[0]

    def test_every_status_is_reachable(self) -> None:
        full, none, part = outcome(), outcome(filled=0), outcome(filled=4)
        unplaced = outcome(ordered=0, filled=0)
        assert self.status(full, full) is ParityStatus.MATCHED
        assert self.status(none, none) is ParityStatus.MATCHED
        assert self.status(None, full) is ParityStatus.BACKTEST_ONLY_SIGNAL
        assert self.status(full, None) is ParityStatus.PAPER_ONLY_SIGNAL
        assert (
            self.status(outcome(ordered=0, filled=0, rejected=True), full)
            is ParityStatus.RISK_REJECTED
        )
        assert self.status(none, full) is ParityStatus.MISSED
        assert self.status(unplaced, full) is ParityStatus.FILLED_BACKTEST_NOT_PAPER
        assert self.status(full, none) is ParityStatus.FILLED_PAPER_NOT_BACKTEST
        assert self.status(part, full) is ParityStatus.PARTIAL
        assert self.status(full, part) is ParityStatus.PARTIAL

    def test_a_risk_rejection_wins_even_with_no_backtest_partner(self) -> None:
        assert self.status(outcome(0, 0, rejected=True), None) is ParityStatus.RISK_REJECTED

    def test_the_reason_says_which_side(self) -> None:
        reason = StatusClassifier().classify(outcome(filled=4), outcome())[1]
        assert "paper" in reason
        assert OrderSide.BUY  # sides are irrelevant to the classifier

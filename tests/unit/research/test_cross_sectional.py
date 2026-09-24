"""EM-179: `CrossSectionalEngine` — ranks the universe by residual momentum and evaluates the
top/bottom tails' forward performance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from tests.unit.research.conftest import START

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize
from emporos.research.costs import TransactionCostModel
from emporos.research.cross_sectional import CrossSectionalEngine, Tail
from emporos.research.factors import AlignedUniverse, BetaEstimator
from emporos.research.horizons import ForwardReturnCalculator, Horizon

SCHEDULE = FeeSchedule(
    name="test",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.1"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.025"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.003"),
    gst_percent=Decimal("18"),
)
FREE = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(0))
SIGNAL_HORIZON = Horizon(timedelta(minutes=5), 1)
HOLDING = ForwardReturnCalculator(Timeframe.M5, (timedelta(minutes=5),))


class AlternatingAxis:
    """A fake regime axis: "even"/"odd" by bar position, deterministic and stateless (mirrors
    `test_engine.py`'s fake)."""

    name = "fake"

    def __init__(self) -> None:
        self._count = -1

    def update(self, candle: Candle) -> str | None:
        self._count += 1
        return "even" if self._count % 2 == 0 else "odd"


def _instrument(instrument_id: str, closes: list[float]) -> list[Candle]:
    out = []
    for i, close in enumerate(closes):
        amount = Decimal(str(close))
        out.append(
            Candle(
                instrument_id=instrument_id,
                timeframe=Timeframe.M5,
                ts=START + i * Timeframe.M5.duration,
                open=Money.of(amount),
                high=Money.of(amount),
                low=Money.of(amount),
                close=Money.of(amount),
                volume=1000,
            )
        )
    return out


def _four_instrument_universe() -> AlignedUniverse:
    """B and C alternate +1%/-1% (so the market factor has variance to regress against); A tracks
    B plus a fixed +2% idiosyncratic edge every bar, D tracks C minus a fixed 2% edge — a clean,
    persistent outperformer and underperformer relative to the market."""
    n = 8
    b_close, c_close = [100.0], [100.0]
    for i in range(1, n):
        r = 0.01 if i % 2 else -0.01
        b_close.append(b_close[-1] * (1 + r))
        c_close.append(c_close[-1] * (1 - r))
    a_close, d_close = [100.0], [100.0]
    for i in range(1, n):
        rb = b_close[i] / b_close[i - 1] - 1
        rc = c_close[i] / c_close[i - 1] - 1
        a_close.append(a_close[-1] * (1 + rb + 0.02))
        d_close.append(d_close[-1] * (1 + rc - 0.02))
    return AlignedUniverse({
        "A": _instrument("A", a_close), "B": _instrument("B", b_close),
        "C": _instrument("C", c_close), "D": _instrument("D", d_close),
    })  # fmt: skip


def _engine(**overrides: object) -> CrossSectionalEngine:
    defaults: dict[str, object] = dict(
        signal_horizons=[SIGNAL_HORIZON],
        holding_returns=HOLDING,
        beta_estimator=BetaEstimator(window=2, min_samples=2),
        cost_model=FREE,
        exchange=Exchange.NSE,
        capital=Money.of(Decimal(50_000)),
        size=DeclaredSize(Decimal(12_500)),
        tail_fraction=Decimal("0.5"),
    )
    defaults.update(overrides)
    return CrossSectionalEngine(**defaults)  # type: ignore[arg-type]


def test_at_least_one_signal_horizon_is_required() -> None:
    with pytest.raises(ValueError, match="signal horizon"):
        _engine(signal_horizons=[])


def test_tail_fraction_must_lie_in_zero_to_half() -> None:
    with pytest.raises(ValueError, match="tail_fraction"):
        _engine(tail_fraction=Decimal("0.6"))
    with pytest.raises(ValueError, match="tail_fraction"):
        _engine(tail_fraction=Decimal("0"))


def test_the_declared_size_is_exposed_for_the_report() -> None:
    assert _engine().size == DeclaredSize(Decimal(12_500))


def test_a_book_that_cannot_be_held_at_the_declared_size_is_refused_not_shrunk() -> None:
    # two tails of two names are four legs; at 25,000 each that is 100,000 against 50,000 capital
    engine = _engine(size=DeclaredSize(Decimal(25_000)))

    with pytest.raises(ValueError, match="declare a smaller size"):
        engine.evaluate(_four_instrument_universe())


def test_cost_model_label_is_exposed() -> None:
    assert _engine().cost_model_label == FREE.label


def test_a_single_instrument_universe_produces_no_segments() -> None:
    universe = AlignedUniverse({"A": _instrument("A", [100.0, 101.0, 102.0])})

    assert _engine().evaluate(universe) == []


def test_the_persistent_outperformer_lands_in_the_top_tail_with_positive_expectancy() -> None:
    segments = _engine().evaluate(_four_instrument_universe())
    pooled = {(s.tail, s.holding_horizon): s.report for s in segments if s.axis is None}

    top = pooled[(Tail.TOP, SIGNAL_HORIZON)]
    bottom = pooled[(Tail.BOTTOM, SIGNAL_HORIZON)]

    assert top.sample_size == bottom.sample_size == 8
    assert top.gross_expectancy is not None and top.gross_expectancy > Decimal(0)
    assert bottom.gross_expectancy is not None and bottom.gross_expectancy < Decimal(0)
    assert top.gross_expectancy > bottom.gross_expectancy
    assert top.hit_rate == Decimal("0.75")
    assert bottom.hit_rate == Decimal("0.25")


def test_costs_never_raise_net_expectancy_above_gross() -> None:
    costly = _engine(cost_model=TransactionCostModel(SCHEDULE, slippage_bps=Decimal(50)))
    segments = costly.evaluate(_four_instrument_universe())

    for segment in segments:
        report = segment.report
        assert report.net_expectancy is not None and report.gross_expectancy is not None
        assert report.net_expectancy <= report.gross_expectancy


def test_conditioning_axes_produce_labeled_segments_alongside_pooled() -> None:
    engine = _engine(conditioning_axes=[AlternatingAxis])
    segments = engine.evaluate(_four_instrument_universe())

    axis_segments = [s for s in segments if s.axis == "fake"]
    assert axis_segments
    assert {s.label for s in axis_segments} <= {"even", "odd"}
    assert any(s.axis is None for s in segments)  # pooled segments still present too

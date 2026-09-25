"""EM-228/229: raw bars to the dataset: artifacts flattened, real gaps traded through."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from tests.unit.research.swing.support import bar, sessions

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.adjustments import AdjustmentLedger
from emporos.research.gap_classes import GapClass
from emporos.research.swing.data import AsOfView
from emporos.research.swing.loading import SwingDatasetBuilder
from emporos.research.swing.regime import IndexSeries

DAYS = sessions(40)
FIRST, LAST = DAYS[0], DAYS[-1]


class Source:
    def __init__(self, bars: dict[str, list[Candle]]) -> None:
        self._bars = bars

    def bars(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        return [
            b for b in self._bars.get(instrument_id, [])
            if first <= b.ts.astimezone(IST).date() <= last
        ]  # fmt: skip


def path(name: str, levels: list[tuple[int, str]]) -> list[Candle]:
    """Flat at each level from its start session on: [(0, '100'), (20, '50')]."""
    out = []
    for i, d in enumerate(DAYS):
        level = next(v for start, v in reversed(levels) if i >= start)
        out.append(bar(name, d, level, level))
    return out


def index(moves: dict[int, str]) -> IndexSeries:
    closes, level = [], Decimal(10000)
    for i in range(len(DAYS)):
        level = level * (1 + Decimal(moves.get(i, "0")))
        closes.append(
            bar(
                "NSE:99926000",
                DAYS[i],
                str(level.quantize(Decimal("0.01"))),
                str(level.quantize(Decimal("0.01"))),
            )
        )
    return IndexSeries(closes)


def build(bars: dict[str, list[Candle]], idx: IndexSeries):  # type: ignore[no-untyped-def]
    return SwingDatasetBuilder(Source(bars), AdjustmentLedger(), idx).build(list(bars), FIRST, LAST)


def test_an_artifact_gap_is_flattened_and_a_real_one_is_kept() -> None:
    bars = {
        "NSE:1": path("NSE:1", [(0, "100"), (20, "50")]),  # a 0.5 step, NIFTY flat: artifact
        "NSE:2": path("NSE:2", [(0, "100"), (20, "80")]),  # -20% on a -13% NIFTY day: real
    }

    built = build(bars, index({20: "-0.13"}))

    artifacts, real = built.of_class(GapClass.ARTIFACT), built.of_class(GapClass.REAL)
    assert [v.finding.instrument_id for v in artifacts] == ["NSE:1"]
    assert [v.finding.instrument_id for v in real] == ["NSE:2"]
    flat = built.dataset.series("NSE:1")
    kept = built.dataset.series("NSE:2")
    assert len({b.close.amount for b in flat.analysis}) == 1  # no step anywhere
    assert flat.neutralised == (DAYS[20],)
    assert kept.neutralised == ()
    assert kept.analysis[19].close.amount == 100
    assert kept.analysis[20].open.amount == 80  # the real gap is in the series


def test_a_signal_lookback_never_sees_a_fake_jump() -> None:
    # X carries an artifact (a 0.5 step at session 20); Y is the same path with no step at all
    step = path("NSE:1", [(0, "100"), (20, "50")])
    clean = path("NSE:2", [(0, "50")])

    built = build({"NSE:1": step, "NSE:2": clean}, index({}))

    view = AsOfView(built.dataset, DAYS[30])
    assert [b.close.amount for b in view.history("NSE:1", 30)] == [
        b.close.amount for b in view.history("NSE:2", 30)
    ]
    assert view.neutralised_within("NSE:1", 30)


def test_a_real_gap_is_never_neutralised_however_large() -> None:
    bars = {"NSE:1": path("NSE:1", [(0, "100"), (20, "70")])}  # -30%, NIFTY -20%: the market moved

    built = build(bars, index({20: "-0.20"}))

    assert [v.gap_class for v in built.verdicts] == [GapClass.REAL]
    assert built.dataset.series("NSE:1").neutralised == ()


def test_every_verdict_carries_a_reason_and_names_without_bars_are_reported() -> None:
    built = SwingDatasetBuilder(
        Source({"NSE:1": path("NSE:1", [(0, "100"), (20, "50")])}), AdjustmentLedger(), index({})
    ).build(["NSE:1", "NSE:404"], FIRST, LAST)

    assert built.names_without_bars == ("NSE:404",)
    assert all(v.reason for v in built.verdicts)

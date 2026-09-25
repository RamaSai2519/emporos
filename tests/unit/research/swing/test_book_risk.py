"""EM-232: the §3.5 book rules and the volatility target, each rule pinned."""

from __future__ import annotations

import statistics
from datetime import date
from decimal import Decimal
from itertools import pairwise
from math import sqrt
from typing import ClassVar

import pytest
from tests.unit.research.swing.support import (
    Always,
    Scripted,
    bar,
    context,
    dataset,
    holding,
    series,
    sessions,
)

from emporos.research.swing.book_risk import BookRiskRules, VolTargeted
from emporos.research.swing.regime import IndexSeries
from emporos.research.swing.rules import Intent

DAYS = sessions(70)  # Mon 2026-01-05 .. mid-March
DATA = dataset(
    series("NSE:1", DAYS, [("100", "100")] * 70), series("NSE:2", DAYS, [("100", "100")] * 70)
)
JAN_END = next(d for d in reversed(DAYS) if d.month == 1)
FEB = [d for d in DAYS if d.month == 2]
MAR = [d for d in DAYS if d.month == 3]


def wants(*names: str) -> Scripted:
    return Scripted(lambda c: [Intent(f"NSE:{n}") for n in names])


def rules(inner: Scripted, regime: bool = True, rebalance: bool = True) -> BookRiskRules:
    return BookRiskRules(inner, Always(rebalance), Always(regime))


def step(r: BookRiskRules, day: date, equity: str, held: tuple[str, ...] = ()) -> list[str]:
    ctx = context(DATA, day, [holding(f"NSE:{h}") for h in held], equity=equity)
    return [i.instrument_id.removeprefix("NSE:") for i in r.desired(ctx)]


class TestPassThrough:
    def test_a_calm_book_gets_whatever_the_inner_strategy_wants(self) -> None:
        r = rules(wants("1", "2"))

        assert step(r, DAYS[1], "100") == ["1", "2"]
        assert step(r, DAYS[2], "101") == ["1", "2"]
        assert (r.record.halts, r.record.kills, r.record.reentries) == ([], [], [])

    @pytest.mark.parametrize("kwargs", [{"month_halt": Decimal(0)}, {"kill": Decimal(1)}])
    def test_the_thresholds_are_fractions(self, kwargs: dict[str, Decimal]) -> None:
        with pytest.raises(ValueError, match="fractions"):
            BookRiskRules(wants(), Always(True), Always(True), **kwargs)


class TestMonthHalt:
    def prime(self) -> BookRiskRules:
        r = rules(wants("1", "2"))
        step(r, JAN_END, "100")  # the close before February's first session: equity 100
        step(r, FEB[0], "99")
        return r

    def test_six_percent_below_the_previous_month_end_halts_entries_for_the_month(self) -> None:
        r = self.prime()

        # holding NSE:1 only; the inner strategy also wants NSE:2: blocked, the holding is kept
        assert step(r, FEB[3], "94", held=("1",)) == ["1"]
        assert r.record.halts == [FEB[3]]

    def test_the_halt_is_at_six_percent_and_not_before(self) -> None:
        r = self.prime()

        assert step(r, FEB[3], "94.01", held=("1",)) == ["1", "2"]
        assert r.record.halts == []
        assert step(r, FEB[4], "94.00", held=("1",)) == ["1"]

    def test_the_halt_lasts_to_the_end_of_the_month_even_if_the_book_recovers(self) -> None:
        r = self.prime()
        step(r, FEB[3], "94", held=("1",))

        assert step(r, FEB[-1], "120", held=("1",)) == ["1"]
        assert r.record.halts == [FEB[3]]  # recorded once

    def test_a_new_month_starts_clean_against_its_own_opening_equity(self) -> None:
        r = self.prime()
        step(r, FEB[3], "94", held=("1",))
        step(r, FEB[-1], "96", held=("1",))  # February closes at 96

        assert step(r, MAR[0], "95", held=("1",)) == ["1", "2"]  # 95 vs 96: only -1%
        assert step(r, MAR[1], "90", held=("1",)) == ["1"]  # 90 vs 96 is -6.25%
        assert r.record.halts == [FEB[3], MAR[1]]

    def test_the_halt_keeps_the_stop_because_it_only_filters_the_inner_wishes(self) -> None:
        r = rules(Scripted(lambda c: []))  # the inner strategy sold everything (a stop)
        step(r, JAN_END, "100")
        step(r, FEB[0], "99")

        assert step(r, FEB[3], "90", held=("1", "2")) == []


class TestKill:
    def killed(self) -> BookRiskRules:
        r = rules(wants("1", "2"))
        step(r, DAYS[1], "100")
        step(r, DAYS[2], "110")  # the peak
        return r

    def test_fifteen_percent_below_the_running_peak_sells_everything(self) -> None:
        r = self.killed()

        assert step(r, DAYS[3], "93.5", held=("1", "2")) == []  # 110 x 0.85 = 93.5
        assert r.record.kills == [DAYS[3]]

    def test_just_inside_the_kill_it_carries_on(self) -> None:
        r = self.killed()

        assert step(r, DAYS[3], "93.51", held=("1", "2")) == ["1", "2"]

    def test_it_stays_in_cash_for_the_rest_of_the_month_however_the_regime_looks(self) -> None:
        r = self.killed()
        step(r, DAYS[3], "90", held=("1", "2"))

        assert step(r, DAYS[4], "90") == []
        assert step(r, DAYS[5], "90") == []
        assert r.record.reentries == []

    def test_it_re_enters_on_the_first_rebalance_of_a_later_month_with_the_regime_on(self) -> None:
        r = self.killed()
        step(r, JAN_END, "90")  # killed in January (the first decision below the line kills)

        assert step(r, FEB[0], "90") == ["1", "2"]
        assert r.record.reentries == [FEB[0]]

    def test_no_re_entry_when_the_regime_is_off_or_it_is_not_a_rebalance_session(self) -> None:
        for regime, rebalance in ((False, True), (True, False)):
            r = rules(wants("1"), regime=regime, rebalance=rebalance)
            step(r, DAYS[1], "100")
            step(r, DAYS[2], "80")  # kill

            assert step(r, FEB[0], "80") == []
            assert r.record.reentries == []

    def test_the_peak_is_reset_at_re_entry(self) -> None:
        r = self.killed()
        step(r, DAYS[3], "90", held=("1", "2"))  # kill against the 110 peak
        step(r, FEB[0], "90")  # re-enter: the peak is now 90

        assert step(r, FEB[1], "85", held=("1",)) == ["1", "2"]  # -5.6%: no halt, no kill
        assert r.record.kills == [DAYS[3]]
        assert step(r, FEB[2], "76.5", held=("1",)) == []  # 90 x 0.85 = 76.5: a second kill
        assert r.record.kills == [DAYS[3], FEB[2]]

    def test_the_kill_comes_before_the_halt(self) -> None:
        r = rules(wants("1"))
        step(r, JAN_END, "100")
        step(r, FEB[0], "99")

        assert step(r, FEB[1], "80", held=("1",)) == []
        assert r.record.halts == []  # a kill is not also logged as a halt


class Index:
    """NIFTY closes from daily returns, for the volatility target."""

    @staticmethod
    def of(returns: list[float], start: int = 0) -> IndexSeries:
        closes, level = [], 10000.0
        for r in returns:
            level *= 1 + r
            closes.append(level)
        return IndexSeries(
            [
                bar("NSE:99926000", DAYS[start + i], f"{c:.6f}", f"{c:.6f}")
                for i, c in enumerate(closes)
            ]
        )


class TestVolTarget:
    ALT: ClassVar[list[float]] = [0.0, *([0.01, -0.01] * 15)]  # 31 closes: 30 returns of +-1%

    def test_exposure_is_the_target_over_the_annualised_sample_sd_of_the_last_20_returns(
        self,
    ) -> None:
        index = Index.of(self.ALT)
        targeted = VolTargeted(wants(), index)
        # the last 20 returns end on the decision close
        last = DAYS[len(self.ALT) - 1]
        closes = index.closes_up_to(last, 21)
        realised = [float(b / a - 1) for a, b in pairwise(closes)]
        sigma = statistics.stdev(realised) * sqrt(252)

        assert targeted.exposure(last) == pytest.approx(Decimal(str(0.15 / sigma)))
        assert Decimal("0.5") < targeted.exposure(last) < Decimal("1")

    def test_it_never_levers_up(self) -> None:
        calm = Index.of([0.0001, -0.0001] * 15)

        assert VolTargeted(wants(), calm).exposure(DAYS[29]) == Decimal(1)

    def test_it_is_full_exposure_when_the_window_is_not_full_or_there_is_no_variation(self) -> None:
        assert VolTargeted(wants(), Index.of([0.01] * 10)).exposure(DAYS[9]) == Decimal(1)
        assert VolTargeted(wants(), Index.of([0.01] * 30)).exposure(DAYS[29]) == Decimal(1)

    def test_only_new_slots_are_scaled_a_holding_is_not_resized(self) -> None:
        turbulent = Index.of([0.0, *([0.03, -0.03] * 15)])
        targeted = VolTargeted(wants("1", "2"), turbulent)
        ctx = context(DATA, DAYS[30], [holding("NSE:1")])

        by_name = {i.instrument_id: i.size_multiple for i in targeted.desired(ctx)}

        assert by_name["NSE:1"] == 1
        assert by_name["NSE:2"] < Decimal("0.5")

    def test_it_reads_only_closes_up_to_the_decision_day(self) -> None:
        wild_later = Index.of([0.0] * 25 + [0.2, -0.2] * 5)

        assert VolTargeted(wants(), wild_later).exposure(DAYS[24]) == Decimal(1)

    @pytest.mark.parametrize("kwargs", [{"target": Decimal(0)}, {"window": 1}])
    def test_the_parameters_are_sensible(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="positive"):
            VolTargeted(wants(), Index.of([0.01] * 3), **kwargs)  # type: ignore[arg-type]

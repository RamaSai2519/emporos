"""EM-237: the C1 weights policy: each rule of the declaration on hand-made price paths."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import dataset, series, sessions

from emporos.research.swing.data import AsOfView
from emporos.research.swing.trend_core import TrendCorePolicy, blend_score, passes_trend

A, B, C = "NSE:1", "NSE:2", "NSE:3"
DAYS = sessions(300)
LAST = DAYS[-1]
FIXED = {A: Decimal("0.4"), B: Decimal("0.3"), C: Decimal("0.3")}


def path(daily: str) -> list[tuple[str, str]]:
    price, out = Decimal(100), []
    for _ in DAYS:
        nxt = price * (1 + Decimal(daily))
        out.append((str(price), str(nxt)))
        price = nxt
    return out


def view(rates: dict[str, str], day: date = LAST, days: list[date] | None = None) -> AsOfView:
    used = days or DAYS
    data = dataset(*(series(n, used, path(r)[: len(used)]) for n, r in rates.items()))
    return AsOfView(data, day)


class TestTheTrendTestAndTheScore:
    def test_a_rising_asset_passes_and_a_falling_one_does_not(self) -> None:
        up = view({A: "0.001"}).history(A, 253)
        down = view({A: "-0.001"}).history(A, 253)

        assert passes_trend(up)
        assert not passes_trend(down)

    def test_the_score_is_the_mean_of_the_3_6_and_12_month_returns(self) -> None:
        bars = view({A: "0.001"}).history(A, 253)
        c = [b.close.amount for b in bars]
        expected = sum((c[-1] / c[-1 - k] - 1 for k in (63, 126, 252)), Decimal(0)) / 3

        assert blend_score(bars) == expected


class TestTheRules:
    def test_top2_holds_the_two_best_trending_assets_half_each(self) -> None:
        policy = TrendCorePolicy("top2", [A, B, C], None)

        weights = policy.targets(view({A: "0.003", B: "0.002", C: "0.001"}))

        assert weights == {A: Decimal("0.5"), B: Decimal("0.5")}

    def test_top2_leaves_the_rest_in_cash_when_fewer_qualify(self) -> None:
        policy = TrendCorePolicy("top2", [A, B, C], None)

        weights = policy.targets(view({A: "0.003", B: "-0.001", C: "-0.002"}))

        assert weights == {A: Decimal("0.5")}
        assert policy.record.months_held == {A: 1}

    def test_top2_all_cash_is_counted(self) -> None:
        policy = TrendCorePolicy("top2", [A, B, C], None)

        assert policy.targets(view({A: "-0.001", B: "-0.001", C: "-0.001"})) == {}
        assert policy.record.months_all_cash == 1

    def test_all_equal_gives_each_trending_asset_a_third_and_the_rest_is_cash(self) -> None:
        policy = TrendCorePolicy("all_equal", [A, B, C], None)

        weights = policy.targets(view({A: "0.003", B: "0.001", C: "-0.001"}))

        assert weights == {A: Decimal(1) / 3, B: Decimal(1) / 3}

    def test_fixed_holds_its_weights_only_while_each_trends(self) -> None:
        policy = TrendCorePolicy("fixed_40_30_30", [A, B, C], FIXED)

        weights = policy.targets(view({A: "0.003", B: "-0.001", C: "0.001"}))

        assert weights == {A: Decimal("0.4"), C: Decimal("0.3")}

    def test_nothing_is_decided_until_every_asset_has_253_sessions(self) -> None:
        policy = TrendCorePolicy("all_equal", [A, B, C], None)
        short = DAYS[:200]

        assert policy.targets(view({A: "0.001", B: "0.001", C: "0.001"}, short[-1], short)) is None
        assert policy.record.first_ranked_day is None

    def test_the_first_ranked_day_is_recorded_once(self) -> None:
        policy = TrendCorePolicy("all_equal", [A, B, C], None)
        v = view({A: "0.001", B: "0.001", C: "0.001"})

        policy.targets(v)
        policy.targets(v)

        assert policy.record.first_ranked_day == LAST
        assert policy.record.decisions == 2


class TestConstruction:
    def test_an_unknown_rule_is_refused(self) -> None:
        with pytest.raises(ValueError, match="the rule is one of"):
            TrendCorePolicy("top3", [A], None)

    def test_fixed_weights_must_cover_the_assets_and_add_up_to_one(self) -> None:
        with pytest.raises(ValueError, match="fixed weights"):
            TrendCorePolicy("fixed_40_30_30", [A, B, C], {A: Decimal("0.5"), B: Decimal("0.5")})
        with pytest.raises(ValueError, match="fixed weights"):
            TrendCorePolicy("fixed_40_30_30", [A, B], {A: Decimal("0.5"), B: Decimal("0.4")})

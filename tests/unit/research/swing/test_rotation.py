"""EM-233: A5's rules, one test per line of the declaration."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.swing.support import Always, context, dataset, holding, series, sessions

from emporos.research.swing.rotation import EtfDualMomentum

DAYS = sessions(300)
NOW = DAYS[-1]


def grow(rate: str, n: int = 300, start: str = "100") -> list[tuple[str, str]]:
    price, out = Decimal(start), []
    for _ in range(n):
        nxt = price * (1 + Decimal(rate))
        out.append((str(price), str(nxt)))
        price = nxt
    return out


def assets(**paths: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    return dataset(*(series(f"NSE:{n}", DAYS[: len(p)], p) for n, p in paths.items()))


def rotation(score: str = "12-1", k: int = 2, rebalance: bool = True, names: str = "ABC"):  # type: ignore[no-untyped-def]
    return EtfDualMomentum(score, k, [f"NSE:{n}" for n in names], Always(rebalance))


def picks(s: EtfDualMomentum, data, *held: str, day=NOW) -> list[str]:  # type: ignore[no-untyped-def]
    ctx = context(data, day, [holding(f"NSE:{h}") for h in held])
    return [i.instrument_id.removeprefix("NSE:") for i in s.desired(ctx)]


TRENDING = assets(A=grow("0.002"), B=grow("0.001"), C=grow("0.0005"))


class TestSelection:
    def test_it_holds_the_top_k_by_score(self) -> None:
        assert picks(rotation(k=2), TRENDING) == ["A", "B"]
        assert picks(rotation(k=1), TRENDING) == ["A"]

    def test_fewer_qualifying_than_k_leaves_the_rest_in_cash(self) -> None:
        data = assets(A=grow("0.002"), B=grow("-0.001"), C=grow("-0.002"))

        assert picks(rotation(k=2), data) == ["A"]

    def test_nothing_qualifying_is_all_cash_and_counted(self) -> None:
        data = assets(A=grow("-0.001"), B=grow("-0.001"), C=grow("-0.002"))
        s = rotation(k=2)

        assert picks(s, data) == []
        assert s.record.months_all_cash == 1

    def test_an_asset_below_its_200_session_average_does_not_qualify_however_strong_its_12_1(
        self,
    ) -> None:
        # C rose strongly for 150 sessions then fell for 150: its 12-1 (close[t-21] vs close[t-252])
        # is still positive, but it sits below its 200-session average
        rising, falling = grow("0.004", 150), grow("-0.003", 150, start="182")
        data = assets(A=grow("0.001"), B=grow("0.0005"), C=rising + falling)

        assert "C" not in picks(rotation(k=3), data)

    def test_ties_break_by_asset_id(self) -> None:
        data = assets(B=grow("0.001"), A=grow("0.001"), C=grow("0.001"))

        assert picks(rotation(k=2), data) == ["A", "B"]

    def test_the_two_scores_can_rank_differently(self) -> None:
        # X rose hard early and has drifted up slowly since; Y rose steadily. 12-1 favours X's early
        # surge, the blend (3, 6 and 12 months) favours Y's recent strength.
        x = grow("0.006", 100) + grow("0.0003", 200, start="182")
        y = grow("0.0012", 300)
        data = assets(X=x, Y=y)

        assert picks(rotation("12-1", 1, names="XY"), data) == ["X"]
        assert picks(rotation("blend", 1, names="XY"), data) == ["Y"]


class TestScores:
    def test_12_1_is_the_close_21_sessions_ago_over_the_close_252_ago(self) -> None:
        # A is flat at 100 until session 100 (t-199), then 150 and drifting up: close[t-252] is 100
        # and close[t-21] about 155, so 12-1 is about +55%, far above B's 2%
        path = [("100", "100")] * 100 + grow("0.0002", 200, start="150")
        data = assets(A=path, B=grow("0.0001"))
        s = rotation("12-1", 1, names="AB")

        assert picks(s, data) == ["A"]

    def test_the_recent_month_is_skipped_by_12_1(self) -> None:
        # A crashes in the last 15 sessions: 12-1 does not see it (but the 200-day filter may)
        path = grow("0.003", 285) + grow("-0.01", 15, start="234")
        data = assets(A=path, B=grow("0.0001"))

        assert picks(rotation("12-1", 1, names="AB"), data) in (["A"], ["B"])


class TestTimingAndWarmUp:
    def test_nothing_is_decided_until_every_asset_has_253_sessions(self) -> None:
        short = assets(A=grow("0.002"), B=grow("0.002"), C=grow("0.002", 200))
        s = rotation(k=2)

        assert picks(s, short, day=DAYS[199]) == []
        assert s.record.first_ranked_day is None

    def test_the_first_ranked_day_is_recorded_once_all_have_the_history(self) -> None:
        s = rotation(k=2)

        picks(s, TRENDING, day=DAYS[260])
        picks(s, TRENDING, day=DAYS[270])

        assert s.record.first_ranked_day == DAYS[260]
        assert s.record.rebalances == 2
        assert s.record.months_held == {"NSE:A": 2, "NSE:B": 2}

    def test_between_rebalances_the_book_is_left_alone(self) -> None:
        s = rotation(k=1, rebalance=False)

        assert picks(s, TRENDING, "C", "B") == ["B", "C"]  # exactly what is held, no ranking

    def test_an_asset_that_did_not_trade_today_is_not_bought(self) -> None:
        data = assets(A=grow("0.002"), B=grow("0.001"), C=grow("0.0005"))
        ctx = context(data, NOW, tradable=["NSE:B", "NSE:C"])

        assert [i.instrument_id for i in rotation(k=1).desired(ctx)] == ["NSE:B"]


class TestValidation:
    @pytest.mark.parametrize(
        ("score", "k", "assets_"), [("momentum", 1, ["A"]), ("12-1", 0, ["A"]), ("12-1", 1, [])]
    )
    def test_bad_parameters_are_refused(self, score: str, k: int, assets_: list[str]) -> None:
        with pytest.raises(ValueError, match="score is one of|at least one"):
            EtfDualMomentum(score, k, assets_, Always(True))

"""EM-229: A2's rules, one test per line of the declaration."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from tests.unit.research.swing.support import Always, context, dataset, holding, series, sessions

from emporos.core.clock import IST
from emporos.research.swing.earnings_drift import PostEarningsDrift, ReactionSessions
from emporos.research.swing.rules import LossStop

DAYS = sessions(30)
REACT = DAYS[25]  # the reaction session under test
FIVE, EIGHT = Decimal("0.05"), Decimal("0.08")


def stamp(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def name(
    jump: str = "0.10", volume: int = 5000, base_volume: int = 1000, n: int = 30, jump_at: int = 25
) -> tuple[list[tuple[str, str]], list[int]]:
    """Flat at 100 until `jump_at`, where it closes `jump` higher on `volume`."""
    prices, volumes = [], []
    for i in range(n):
        close = Decimal(100) * (1 + Decimal(jump)) if i >= jump_at else Decimal(100)
        prices.append(("100", str(close)) if i == jump_at else (str(close), str(close)))
        volumes.append(volume if i == jump_at else base_volume)
    return prices, volumes


def data_for(**cases: tuple[list[tuple[str, str]], list[int]]):  # type: ignore[no-untyped-def]
    return dataset(
        *(series(f"NSE:{k}", DAYS[: len(p)], p, volumes=v) for k, (p, v) in cases.items())
    )


def reactions(*names: str, day: date = REACT) -> ReactionSessions:
    return ReactionSessions(
        {day: frozenset(f"NSE:{n}" for n in names)}, {day.year: len(names)}, 0, 0
    )


def drift(r: ReactionSessions, reaction: Decimal = FIVE, hold: int = 20, n: int = 5,
          regime: bool = True) -> PostEarningsDrift:  # fmt: skip
    return PostEarningsDrift(reaction, hold, n, r, Always(regime), LossStop())


def picks(s: PostEarningsDrift, data, *held, day: date = REACT, tradable=None) -> list[str]:  # type: ignore[no-untyped-def]
    ctx = context(data, day, list(held), tradable)
    return [i.instrument_id.removeprefix("NSE:") for i in s.desired(ctx)]


class TestReactionSessions:
    SESSIONS: ClassVar[dict[str, list[date]]] = {"NSE:1": DAYS}

    def build(self, *moments: datetime) -> ReactionSessions:
        return ReactionSessions.build({"NSE:1": list(moments)}, self.SESSIONS)

    def test_before_the_open_the_same_session_reacts(self) -> None:
        r = self.build(stamp(DAYS[5], 8, 30))

        assert r.reacting_on(DAYS[5]) == {"NSE:1"}

    def test_after_the_close_the_next_session_reacts(self) -> None:
        r = self.build(stamp(DAYS[5], 17, 0))

        assert r.reacting_on(DAYS[6]) == {"NSE:1"}
        assert r.reacting_on(DAYS[5]) == frozenset()

    def test_a_friday_evening_result_reacts_on_monday(self) -> None:
        friday = next(d for d in DAYS if d.weekday() == 4)
        monday = DAYS[DAYS.index(friday) + 1]

        assert self.build(stamp(friday, 19, 0)).reacting_on(monday) == {"NSE:1"}

    def test_in_session_is_skipped_and_counted(self) -> None:
        r = self.build(stamp(DAYS[5], 11, 0))

        assert r.in_session == 1
        assert r.reacting_on(DAYS[5]) == frozenset()
        assert r.used_by_year == {}

    def test_used_sessions_are_counted_per_year(self) -> None:
        r = self.build(stamp(DAYS[5], 8, 0), stamp(DAYS[9], 18, 0))

        assert r.used_by_year == {2026: 2}

    def test_an_event_outside_the_names_sessions_is_not_used(self) -> None:
        r = self.build(stamp(date(2020, 1, 6), 8, 0))

        assert r.used_by_year == {}

    def test_a_name_with_no_sessions_yields_nothing(self) -> None:
        r = ReactionSessions.build({"NSE:9": [stamp(DAYS[5], 8, 0)]}, {})

        assert r.reacting_on(DAYS[5]) == frozenset()


class TestQualification:
    DATA = data_for(A=name("0.10", 5000))

    def test_a_strong_reaction_on_heavy_volume_is_bought(self) -> None:
        assert picks(drift(reactions("A")), self.DATA) == ["A"]

    def test_the_reaction_must_reach_the_threshold(self) -> None:
        assert picks(drift(reactions("A"), reaction=Decimal("0.12")), self.DATA) == []
        assert picks(drift(reactions("A"), reaction=Decimal("0.10")), self.DATA) == ["A"]  # equal

    def test_volume_must_be_at_least_twice_the_mean_of_the_20_sessions_before(self) -> None:
        light = data_for(A=name("0.10", volume=1999))
        exactly = data_for(A=name("0.10", volume=2000))

        assert picks(drift(reactions("A")), light) == []
        assert picks(drift(reactions("A")), exactly) == ["A"]

    def test_a_drop_is_not_a_reaction(self) -> None:
        assert picks(drift(reactions("A")), data_for(A=name("-0.10"))) == []

    def test_only_names_whose_results_reacted_that_session_are_considered(self) -> None:
        data = data_for(A=name("0.10"), B=name("0.10"))

        assert picks(drift(reactions("A")), data) == ["A"]
        assert (
            picks(
                drift(
                    reactions("A"),
                ),
                data,
                day=DAYS[24],
            )
            == []
        )

    def test_a_name_needs_the_reaction_session_and_the_20_before_it(self) -> None:
        def result(n: int) -> list[str]:
            short = name("0.10", n=n, jump_at=n - 1)
            return picks(drift(reactions("A", day=DAYS[n - 1])), data_for(A=short), day=DAYS[n - 1])

        assert result(20) == []
        assert result(21) == ["A"]

    def test_a_name_that_did_not_trade_or_is_already_held_is_not_bought(self) -> None:
        data = data_for(A=name("0.10"))

        assert picks(drift(reactions("A")), data, tradable=[]) == []
        held = holding("NSE:A", sessions_held=3, price="90")
        assert picks(drift(reactions("A")), data, held) == ["A"]  # kept, not bought twice

    def test_the_adjusted_return_is_used_not_the_raw_one(self) -> None:
        from emporos.research.adjustments import ActionKind, AdjustmentFactor, AdjustmentLedger

        # a 1:2 bonus with the ex-date on the reaction session: raw close halves, adjusted is flat
        prices = [("100", "100")] * 25 + [("50", "50")] * 5
        volumes = [1000] * 25 + [5000] + [1000] * 4
        ledger = AdjustmentLedger(
            [AdjustmentFactor("NSE:A", REACT, Decimal("0.5"), ActionKind.BONUS, "s")]
        )
        data = dataset(series("NSE:A", DAYS, prices, ledger, volumes=volumes))

        assert picks(drift(reactions("A")), data) == []


class TestSlots:
    def test_the_largest_reactions_take_the_free_slots_the_rest_are_skipped_not_queued(
        self,
    ) -> None:
        data = data_for(A=name("0.06"), B=name("0.12"), C=name("0.09"))
        s = drift(reactions("A", "B", "C"), n=2)

        assert picks(s, data) == ["B", "C"]
        assert s.record.skipped_for_slots == 1

    def test_holdings_use_up_slots(self) -> None:
        data = data_for(A=name("0.06"), B=name("0.12"), H=name("0.0", 1000, jump_at=99))
        held = holding("NSE:H", sessions_held=2, price="90")

        assert picks(drift(reactions("A", "B"), n=2), data, held) == ["H", "B"]

    def test_a_full_book_takes_nothing(self) -> None:
        data = data_for(A=name("0.10"), H=name("0", jump_at=99))
        held = holding("NSE:H", sessions_held=2, price="90")

        assert picks(drift(reactions("A"), n=1), data, held) == ["H"]

    def test_a_slot_freed_by_an_exit_is_available_at_the_same_close(self) -> None:
        data = data_for(A=name("0.10"), H=name("0", jump_at=99))
        due = holding("NSE:H", sessions_held=20, price="90")

        assert picks(drift(reactions("A"), n=1), data, due) == ["A"]

    def test_qualifiers_are_counted_per_year(self) -> None:
        s = drift(reactions("A"))

        picks(s, data_for(A=name("0.10")))

        assert s.record.qualified_by_year == {2026: 1}


class TestExitsAndRegime:
    DATA = data_for(A=name("0.10"), H=name("0", jump_at=99))

    def test_a_holding_is_sold_once_held_for_h_sessions(self) -> None:
        s = drift(reactions(), hold=20)

        assert picks(s, self.DATA, holding("NSE:H", sessions_held=19, price="90")) == ["H"]
        assert picks(s, self.DATA, holding("NSE:H", sessions_held=20, price="90")) == []

    def test_a_holding_at_its_stop_is_sold_early(self) -> None:
        s = drift(reactions())

        assert picks(s, self.DATA, holding("NSE:H", sessions_held=3, price="500")) == []
        assert s.record.stops == 1

    def test_the_regime_blocks_entries_but_never_sells(self) -> None:
        s = drift(reactions("A"), regime=False)
        held = holding("NSE:H", sessions_held=3, price="90")

        assert picks(s, self.DATA, held) == ["H"]  # kept; A (a qualifier) is not bought

    def test_the_regime_off_and_nothing_held_is_cash(self) -> None:
        assert picks(drift(reactions("A"), regime=False), self.DATA) == []

    @pytest.mark.parametrize("kwargs", [{"reaction": Decimal(0)}, {"hold": 0}, {"n": 0}])
    def test_the_parameters_are_positive(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="positive"):
            drift(reactions(), **kwargs)  # type: ignore[arg-type]

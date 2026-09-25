"""EM-221: corporate-action subjects to factors, the ledger with provenance, the factor build."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.research.adjustments import ActionKind
from emporos.research.corporate_actions import (
    CorporateAction,
    CorporateActionLedger,
    FactorBuilder,
    SubjectInterpreter,
    parse_actions,
)

D = date(2026, 1, 5)
INTERPRET = SubjectInterpreter().interpret


class TestInterpreter:
    @pytest.mark.parametrize(
        ("subject", "ratio"),
        [
            ("Bonus 1:1", Decimal("0.5")),
            ("Bonus 1:2", Decimal(2) / Decimal(3)),
            ("Bonus 2:1", Decimal(1) / Decimal(3)),
            ("Bonus issue 3:5", Decimal(5) / Decimal(8)),
        ],
    )
    def test_a_bonus_of_a_for_b_multiplies_the_price_by_b_over_a_plus_b(
        self, subject: str, ratio: Decimal
    ) -> None:
        reading = INTERPRET(subject)

        assert reading.factors == ((ActionKind.BONUS, ratio),)
        assert not reading.needs_review

    @pytest.mark.parametrize(
        ("subject", "ratio"),
        [
            ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share",
             Decimal("0.2")),
            ("Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share",
             Decimal("0.2")),
            ("Face Value Split (Sub-Division) - From Rs 2/- Per Share To Rs 1/- Per Share",
             Decimal("0.5")),
            ("Face Value Split (Sub-Division) - From Rs 10 Per Share To Rs 1 Per Share",
             Decimal("0.1")),
        ],
    )  # fmt: skip
    def test_a_face_value_split_multiplies_the_price_by_new_over_old(
        self, subject: str, ratio: Decimal
    ) -> None:
        reading = INTERPRET(subject)

        assert reading.factors == ((ActionKind.SPLIT, ratio),)
        assert not reading.needs_review

    def test_a_consolidation_raises_the_price_and_is_kind_other(self) -> None:
        reading = INTERPRET("Face Value Consolidation - From Rs 1/- Per Share To Rs 10/- Per Share")

        assert reading.factors == ((ActionKind.OTHER, Decimal(10)),)

    def test_a_bonus_and_a_split_in_one_subject_give_both_factors(self) -> None:
        reading = INTERPRET(
            "Bonus 1:1 / Face Value Split (Sub-Division) - "
            "From Rs 10/- Per Share To Rs 5/- Per Share"
        )

        assert {k for k, _ in reading.factors} == {ActionKind.BONUS, ActionKind.SPLIT}
        assert not reading.needs_review

    @pytest.mark.parametrize(
        "subject",
        ["Rights 1:15 @ Premium Rs 1247", "Demerger", "Amalgamation", "Scheme Of Arrangement",
         "Capital Reduction", "Face Value Split (Sub-Division) - text nobody can read"],
    )  # fmt: skip
    def test_a_price_affecting_action_with_no_ratio_goes_to_review_unadjusted(
        self, subject: str
    ) -> None:
        reading = INTERPRET(subject)

        assert reading.factors == ()
        assert reading.needs_review

    def test_a_bonus_together_with_a_rights_issue_is_adjusted_and_still_reviewed(self) -> None:
        reading = INTERPRET("Bonus 1:1 and Rights 1:5")

        assert reading.factors == ((ActionKind.BONUS, Decimal("0.5")),)
        assert reading.needs_review

    @pytest.mark.parametrize(
        "subject",
        ["Dividend - Rs 11/- Per Share", "Annual General Meeting/Dividend - Rs 6.50 Per Share",
         "Interim Dividend", "", "Buy Back"],
    )  # fmt: skip
    def test_cash_and_meetings_are_neither_adjusted_nor_reviewed(self, subject: str) -> None:
        reading = INTERPRET(subject)

        assert reading.factors == ()
        assert not reading.needs_review

    def test_a_same_size_face_value_is_no_factor(self) -> None:
        assert INTERPRET("Split - From Rs 10/- Per Share To Rs 10/- Per Share").factors == ()


class TestParse:
    def test_rows_become_actions_oldest_first_with_the_subject_cleaned(self) -> None:
        payload = [
            {"symbol": "X", "exDate": "07-Sep-2017", "subject": "  Bonus   1:1 ", "isin": "I",
             "series": "EQ"},
            {"symbol": "X", "exDate": "13-Jul-2017", "subject": "Dividend - Rs 11", "isin": "I",
             "series": "EQ"},
        ]  # fmt: skip

        actions = parse_actions(payload, "X")

        assert [(a.ex_date, a.subject) for a in actions] == [
            (date(2017, 7, 13), "Dividend - Rs 11"), (date(2017, 9, 7), "Bonus 1:1"),
        ]  # fmt: skip

    def test_a_row_without_a_usable_ex_date_is_skipped(self) -> None:
        payload = [{"exDate": "-", "subject": "Bonus 1:1"}, {"subject": "Bonus 1:1"}]

        assert parse_actions(payload, "X") == []

    @pytest.mark.parametrize("payload", [{"error": "x"}, "nope", [1, 2]])
    def test_a_payload_that_is_not_a_list_of_objects_is_an_error(self, payload: object) -> None:
        with pytest.raises(ValueError, match="X"):
            parse_actions(payload, "X")


class TestLedger:
    def ledger(self, tmp_path: Path) -> CorporateActionLedger:
        return CorporateActionLedger(tmp_path / "a.jsonl", tmp_path / "c.jsonl")

    def test_records_carry_their_source_and_fetch_date_and_are_kept_once(
        self, tmp_path: Path
    ) -> None:
        ledger = self.ledger(tmp_path)
        action = CorporateAction("X", D, "Bonus 1:1", "I", "EQ")
        when = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

        first = ledger.record(
            "X", [action], date(2016, 10, 3), date(2026, 3, 18), when, "https://u"
        )
        second = ledger.record(
            "X", [action], date(2016, 10, 3), date(2026, 3, 18), when, "https://u"
        )

        assert (first, second) == (1, 0)
        assert ledger.actions() == [action]
        line = (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()[0]
        assert '"source_url": "https://u"' in line
        assert '"fetched_on": "2026-09-25"' in line

    def test_a_symbol_with_no_actions_is_still_marked_collected(self, tmp_path: Path) -> None:
        ledger = self.ledger(tmp_path)

        ledger.record("Y", [], date(2016, 10, 3), date(2026, 3, 18), datetime.now(UTC), "u")

        assert ledger.collected_symbols() == frozenset({"Y"})
        assert ledger.actions() == []

    def test_an_absent_ledger_reads_as_empty(self, tmp_path: Path) -> None:
        ledger = self.ledger(tmp_path)

        assert ledger.actions() == []
        assert ledger.collected_symbols() == frozenset()


class TestFactorBuilder:
    def test_splits_and_bonuses_become_factors_for_the_research_names_only(self) -> None:
        actions = [
            CorporateAction("X", D, "Bonus 1:1"),
            CorporateAction("X", date(2026, 2, 1), "Rights 1:15 @ Premium Rs 1247"),
            CorporateAction("X", date(2026, 3, 1), "Dividend - Rs 6"),
            CorporateAction("HELD", D, "Bonus 1:1"),
        ]

        built = FactorBuilder().build(actions, {"X": "NSE:1"}, "a source")

        (factor,) = built.ledger.factors_for("NSE:1")
        assert (factor.ex_date, factor.ratio, factor.kind) == (D, Decimal("0.5"), ActionKind.BONUS)
        assert factor.source == "a source"
        assert factor.note == "Bonus 1:1"
        assert [a.subject for a in built.review] == ["Rights 1:15 @ Premium Rs 1247"]
        assert built.ignored == 1
        assert built.ledger.factors_for("HELD") == ()

    def test_the_same_action_listed_twice_is_one_factor(self) -> None:
        actions = [CorporateAction("X", D, "Bonus 1:1", series="EQ"),
                   CorporateAction("X", D, "Bonus 1:1", series="BE")]  # fmt: skip

        assert len(FactorBuilder().build(actions, {"X": "NSE:1"}, "s").ledger) == 1

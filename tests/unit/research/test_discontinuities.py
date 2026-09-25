"""EM-221: every >=15% open discontinuity is explained by a factor on record or quarantined."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest
from tests.unit.research.test_adjustments import D1, D2, D3, X, daily, factor

from emporos.research.adjustments import ActionKind, AdjustmentLedger
from emporos.research.discontinuities import (
    DiscontinuityAudit,
    DiscontinuityStatus,
    split_shape,
)

NO_LEDGER = AdjustmentLedger()


class TestShape:
    @pytest.mark.parametrize(
        ("ratio", "shape"),
        [("0.5", Fraction(1, 2)), ("0.52", Fraction(1, 2)), ("0.2", Fraction(1, 5)),
         ("0.1", Fraction(1, 10)), ("0.667", Fraction(2, 3)), ("2", Fraction(2)),
         ("10", Fraction(10))],
    )  # fmt: skip
    def test_a_ratio_near_a_split_or_bonus_ratio_has_that_shape(
        self, ratio: str, shape: Fraction
    ) -> None:
        assert split_shape(Decimal(ratio)) == shape

    @pytest.mark.parametrize("ratio", ["0.83", "1.2", "0.9", "1.35", "0.45"])
    def test_an_ordinary_gap_has_none(self, ratio: str) -> None:
        assert split_shape(Decimal(ratio)) is None


class TestAudit:
    def test_a_split_on_record_that_closes_the_gap_is_explained(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "200", "201")]
        ledger = AdjustmentLedger([factor(D2, "0.2")])

        (found,) = DiscontinuityAudit(ledger).audit(X, raw).findings

        assert found.status is DiscontinuityStatus.EXPLAINED
        assert (found.day, found.raw_ratio, found.adjusted_ratio) == (
            D2,
            Decimal("0.2"),
            Decimal(1),
        )

    def test_a_factor_that_leaves_the_gap_open_is_mismatched_and_quarantined(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "200", "201")]
        ledger = AdjustmentLedger([factor(D2, "0.5", ActionKind.BONUS)])  # the wrong ratio

        report = DiscontinuityAudit(ledger).audit(X, raw)

        assert report.findings[0].status is DiscontinuityStatus.MISMATCHED
        assert report.quarantined == ((X, D2),)

    def test_a_split_shaped_gap_with_no_factor_is_quarantined_as_such(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "500", "505")]

        report = DiscontinuityAudit(NO_LEDGER).audit(X, raw)

        assert report.findings[0].status is DiscontinuityStatus.SPLIT_SHAPED
        assert report.findings[0].shape == Fraction(1, 2)
        assert report.quarantined == ((X, D2),)

    def test_a_large_gap_that_is_not_split_shaped_is_unexplained_and_quarantined(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "1200", "1210")]  # +20%: results, or not

        report = DiscontinuityAudit(NO_LEDGER).audit(X, raw)

        assert report.findings[0].status is DiscontinuityStatus.UNEXPLAINED
        assert report.quarantined == ((X, D2),)

    def test_a_gap_under_the_limit_is_not_a_finding(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "860", "870")]  # -14%

        assert DiscontinuityAudit(NO_LEDGER).audit(X, raw).findings == ()

    def test_the_gap_is_open_against_the_previous_close_not_the_previous_open(self) -> None:
        raw = [daily(D1, "1000", "800"), daily(D2, "790", "800")]  # closes at -20%, opens at -1.3%

        assert DiscontinuityAudit(NO_LEDGER).audit(X, raw).findings == ()

    def test_the_limit_itself_counts(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "850", "850")]  # exactly -15%

        assert len(DiscontinuityAudit(NO_LEDGER).audit(X, raw).findings) == 1

    def test_a_small_bonus_is_adjusted_without_being_a_finding(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "910", "910")]  # 1:10 bonus is -9%
        ledger = AdjustmentLedger([factor(D2, "0.9091", ActionKind.BONUS)])

        assert DiscontinuityAudit(ledger).audit(X, raw).findings == ()

    def test_an_ex_date_on_a_holiday_before_the_gap_explains_it(self) -> None:
        raw = [daily(D2, "1000", "1000"), daily(D3, "200", "200")]  # Fri -> Mon
        ledger = AdjustmentLedger([factor(date(2026, 1, 3), "0.2")])  # a Saturday

        assert DiscontinuityAudit(ledger).audit(X, raw).findings[0].status is (
            DiscontinuityStatus.EXPLAINED
        )

    def test_a_factor_on_another_day_does_not_explain_the_gap(self) -> None:
        raw = [daily(D1, "1000", "1000"), daily(D2, "1000", "1000"), daily(D3, "200", "200")]
        ledger = AdjustmentLedger([factor(D2, "0.2")])  # a day early: D1..D2 now scaled, D3 not

        statuses = [f.status for f in DiscontinuityAudit(ledger).audit(X, raw).findings]

        assert DiscontinuityStatus.EXPLAINED not in statuses
        assert statuses  # the gap into D3 stays open

    def test_the_report_counts_sessions_and_statuses_across_instruments(self) -> None:
        a = [daily(D1, "1000", "1000"), daily(D2, "500", "500"), daily(D3, "500", "500")]
        b = [
            daily(D1, "100", "100", instrument="NSE:2"),
            daily(D2, "130", "130", instrument="NSE:2"),
        ]

        report = DiscontinuityAudit(NO_LEDGER).audit_all([(X, a), ("NSE:2", b)])

        assert report.sessions_checked == 5
        assert report.by_status()[DiscontinuityStatus.SPLIT_SHAPED] == 1
        assert report.by_status()[DiscontinuityStatus.UNEXPLAINED] == 1
        assert set(report.quarantined) == {(X, D2), ("NSE:2", D2)}

    def test_a_bad_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            DiscontinuityAudit(NO_LEDGER, Decimal("1.5"))


def test_the_report_document_names_the_ledger_and_lists_findings_in_order() -> None:
    raw = [daily(D1, "1000", "1000"), daily(D2, "500", "500"), daily(D3, "700", "700")]

    document = DiscontinuityAudit(NO_LEDGER).audit(X, raw).to_document("abc")

    assert document["ledger_hash"] == "abc"
    assert document["sessions_checked"] == 3
    assert document["by_status"]["split_shaped"] == 1
    assert [f["day"] for f in document["findings"]] == ["2026-01-02", "2026-01-05"]
    assert document["findings"][0] == {
        "instrument_id": X, "day": "2026-01-02", "raw_ratio": "0.5000",
        "status": "split_shaped", "shape": "1/2",
    }  # fmt: skip

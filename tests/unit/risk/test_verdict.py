from __future__ import annotations

import pytest

from emporos.risk.verdict import RuleVerdict


def test_a_block_records_its_reason_and_details_as_text() -> None:
    verdict = RuleVerdict.block("too big", quantity=5, reason="x")
    assert not verdict.allowed and verdict.reason == "too big"
    assert dict(verdict.details) == {"quantity": "5", "reason": "x"}


def test_details_cannot_be_changed_after_the_fact() -> None:
    verdict = RuleVerdict.block("too big", quantity=5)
    with pytest.raises(TypeError):
        verdict.details["quantity"] = "6"  # type: ignore[index]


def test_a_block_without_a_reason_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="say why"):
        RuleVerdict(False, "  ")
    with pytest.raises(ValueError, match="say why"):
        RuleVerdict.block("")


def test_allow_needs_no_reason() -> None:
    assert RuleVerdict.allow().allowed


def test_a_snapshot_and_a_working_order_need_utc_times() -> None:
    from datetime import UTC, datetime

    from emporos.domain.orders import OrderSide
    from emporos.risk.snapshot import RiskSnapshot, WorkingOrder

    naive = datetime(2026, 1, 5, 4, 0)
    with pytest.raises(ValueError, match="UTC"):
        RiskSnapshot(now=naive)
    with pytest.raises(ValueError, match="UTC"):
        WorkingOrder("NSE:1", OrderSide.BUY, naive)
    assert RiskSnapshot(now=naive.replace(tzinfo=UTC)).describe()["markets"] == {}

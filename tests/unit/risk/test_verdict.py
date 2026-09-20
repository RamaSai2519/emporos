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

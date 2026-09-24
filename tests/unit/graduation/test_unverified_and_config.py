"""EM-186 is not finished: until it is, broker evidence is a refusal, never a pass (EM-189)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.broker_verification import CRITICAL_BROKER_CHECKS, CheckOutcome
from emporos.domain.graduation import GraduationStage
from emporos.graduation.config import DEFAULT_GRADUATION_FILE, GraduationSettingsLoader
from emporos.graduation.requirements import BrokerVerificationPassed, PromotionRequest
from emporos.graduation.unverified import UnverifiedBrokerEvidence
from tests.support.graduation import HASH, NOW, STRATEGY


async def test_every_critical_check_is_reported_unknown_with_no_time() -> None:
    checks = await UnverifiedBrokerEvidence().critical_checks()

    assert [c.name for c in checks] == list(CRITICAL_BROKER_CHECKS)
    assert all(c.outcome is CheckOutcome.UNKNOWN and c.checked_at is None for c in checks)


async def test_the_requirement_refuses_and_names_every_check() -> None:
    rule = BrokerVerificationPassed(UnverifiedBrokerEvidence(), FixedClock(NOW), timedelta(days=7))
    request = PromotionRequest(
        STRATEGY, HASH, GraduationStage.PAPER, GraduationStage.LIVE_CONSERVATIVE, "rama"
    )

    got = await rule.assess(request)

    assert got.refusal is not None
    assert all(f"{name}: unknown" in got.refusal for name in CRITICAL_BROKER_CHECKS)


def test_the_shipped_settings_load_and_allow_no_assumed_instrument() -> None:
    settings = GraduationSettingsLoader(DEFAULT_GRADUATION_FILE).load()

    assert settings.broker_verification_max_age == timedelta(days=7)
    assert settings.allowed_assumed_instruments == ()


def test_settings_refuse_a_missing_file_a_bad_document_and_an_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read"):
        GraduationSettingsLoader(tmp_path / "nope.yaml").load()
    path = tmp_path / "g.yaml"
    path.write_text("a: [unclosed")
    with pytest.raises(ConfigurationError, match="not valid YAML"):
        GraduationSettingsLoader(path).load()
    path.write_text("- a\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        GraduationSettingsLoader(path).load()
    path.write_text("broker_verification_max_age_days: 0\n")
    with pytest.raises(ConfigurationError, match="broker_verification_max_age_days"):
        GraduationSettingsLoader(path).load()
    path.write_text("broker_verification_max_age_days: 7\nmax_age: 1\n")
    with pytest.raises(ConfigurationError, match="max_age"):
        GraduationSettingsLoader(path).load()

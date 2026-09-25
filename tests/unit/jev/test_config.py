from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.core.errors import ConfigurationError
from emporos.jev.config import JevConfig, JevCredentialsMissing, require_credentials


def test_disabled_by_default() -> None:
    assert JevConfig().enabled is False


def test_fail_closed_by_default() -> None:
    assert JevConfig().fail_open is False


def test_unknown_mode_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="mode"):
        JevConfig(mode="nonsense")


def test_confidence_threshold_out_of_range_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="confidence_threshold"):
        JevConfig(confidence_threshold=1.5)


def test_max_concurrency_must_be_at_least_one() -> None:
    with pytest.raises(ConfigurationError, match="max_concurrency"):
        JevConfig(max_concurrency=0)


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ConfigurationError, match="timeout_seconds"):
        JevConfig(timeout_seconds=0)


def test_max_retries_cannot_be_negative() -> None:
    with pytest.raises(ConfigurationError, match="max_retries"):
        JevConfig(max_retries=-1)


def test_base_url_cannot_be_empty() -> None:
    with pytest.raises(ConfigurationError, match="base_url"):
        JevConfig(base_url="")


def test_model_cannot_be_empty() -> None:
    with pytest.raises(ConfigurationError, match="model"):
        JevConfig(model="")


def test_require_credentials_returns_the_key_when_present() -> None:
    assert require_credentials(JevConfig(enabled=True), "vck_test") == "vck_test"


def test_require_credentials_raises_an_actionable_error_when_missing() -> None:
    with pytest.raises(JevCredentialsMissing, match="VERCEL_GATEWAY_KEY"):
        require_credentials(JevConfig(enabled=True), None)


def test_require_credentials_raises_for_an_empty_string_key() -> None:
    with pytest.raises(JevCredentialsMissing):
        require_credentials(JevConfig(enabled=True), "")


def test_provenance_fields_default_to_undeclared() -> None:
    config = JevConfig()

    assert config.model_knowledge_cutoff is None
    assert config.inr_per_1k_tokens is None


def test_a_negative_inr_rate_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="inr_per_1k_tokens"):
        JevConfig(inr_per_1k_tokens=Decimal("-0.1"))


def test_jev_and_the_event_trader_aim_at_the_same_gateway_host() -> None:
    from emporos.eventtrader.llm.http_clients import GATEWAY_URL

    assert JevConfig().base_url == GATEWAY_URL == "https://ai-gateway.vercel.sh"

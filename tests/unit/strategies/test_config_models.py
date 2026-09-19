from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from emporos.strategies.config import (
    ExecutionSettings,
    ResolvedStrategyConfig,
    RiskSettings,
    SessionSettings,
    StrategyParameters,
    UniverseMember,
)
from tests.support.strategies import ThresholdParameters, make_config


def _risk(**overrides: Any) -> dict[str, Any]:
    return {
        "max_position_value": "50000",
        "max_open_positions": 3,
        "stop_loss_pct": "1.0",
        "target_pct": "2.0",
    } | overrides


@pytest.mark.parametrize("value", [1.5, 0.1, True, None, [1], {"a": 1}, "abc", "NaN", "Infinity"])
def test_a_money_like_value_must_be_an_exact_string_or_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        RiskSettings.model_validate(_risk(stop_loss_pct=value))


@pytest.mark.parametrize("value", ["1.0", "0.5", 1, Decimal("2.25"), " 3 "])
def test_exact_values_are_accepted_as_decimals(value: object) -> None:
    assert RiskSettings.model_validate(_risk(target_pct=value)).target_pct > 0


def test_bounds_are_enforced() -> None:
    for field, bad in [
        ("max_position_value", "0"),
        ("max_position_value", "-5"),
        ("stop_loss_pct", "0"),
        ("stop_loss_pct", "100"),
        ("target_pct", "0"),
        ("max_open_positions", 0),
    ]:
        with pytest.raises(ValidationError):
            RiskSettings.model_validate(_risk(**{field: bad}))


@pytest.mark.parametrize("bad", ["3", 3.0, True, 0, -1])
def test_integers_are_strict(bad: object) -> None:
    with pytest.raises(ValidationError):
        RiskSettings.model_validate(_risk(max_open_positions=bad))


def test_unknown_keys_are_rejected_in_every_section() -> None:
    with pytest.raises(ValidationError, match="extra"):
        RiskSettings.model_validate(_risk(max_position_valeu="1"))
    with pytest.raises(ValidationError, match="extra"):
        ExecutionSettings.model_validate(
            {"limit_buffer_bps": 5, "reprice_after_seconds": 30, "max_reprices": 3, "x": 1}
        )
    with pytest.raises(ValidationError, match="extra"):
        StrategyParameters.model_validate({"anything": 1})


def test_execution_settings_bounds() -> None:
    good = {"limit_buffer_bps": "2.5", "reprice_after_seconds": 30, "max_reprices": 0}
    assert ExecutionSettings.model_validate(good).max_reprices == 0
    for field, bad in [
        ("limit_buffer_bps", "-1"),
        ("reprice_after_seconds", 0),
        ("max_reprices", -1),
    ]:
        with pytest.raises(ValidationError):
            ExecutionSettings.model_validate(good | {field: bad})


@pytest.mark.parametrize("bad", [900, 15.0, "3pm", "1500", "15:0", "25:00", "15:60", None, True])
def test_a_time_of_day_must_be_a_quoted_hh_mm_string(bad: object) -> None:
    """Unquoted `15:00` is the integer 900 in YAML 1.1 — it must not be read as 00:15."""
    with pytest.raises(ValidationError):
        SessionSettings.model_validate({"no_new_entries_after": bad, "square_off_at": "15:15"})


def test_entries_must_stop_before_the_square_off() -> None:
    for entries, square in [("15:15", "15:15"), ("15:30", "15:15")]:
        with pytest.raises(ValidationError, match="earlier"):
            SessionSettings.model_validate(
                {"no_new_entries_after": entries, "square_off_at": square}
            )


def test_the_universe_needs_members_and_no_duplicates() -> None:
    member = UniverseMember(symbol="NSE:A-EQ", instrument_id="NSE:1")
    twin = UniverseMember(symbol="NSE:B-EQ", instrument_id="NSE:1")
    good = make_config().model_dump() | {"parameters": StrategyParameters()}

    with pytest.raises(ValidationError):
        ResolvedStrategyConfig.model_validate(good | {"universe": ()})
    with pytest.raises(ValidationError, match="twice"):
        ResolvedStrategyConfig.model_validate(good | {"universe": (member, twin)})


def test_a_resolved_config_is_immutable_and_exposes_instrument_ids() -> None:
    config = make_config(instruments=("NSE:1001", "NSE:1002"))
    assert config.instrument_ids == ("NSE:1001", "NSE:1002")
    with pytest.raises(ValidationError):
        config.enabled = False  # type: ignore[misc]


def test_a_dump_keeps_the_strategys_own_parameters() -> None:
    config = make_config(name="threshold", parameters=ThresholdParameters(threshold=Decimal(100)))
    assert config.model_dump()["parameters"] == {"threshold": Decimal(100), "quantity": 1}

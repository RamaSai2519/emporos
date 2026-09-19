from __future__ import annotations

from typing import Any

import pytest

from emporos.domain.candles import Timeframe
from emporos.strategies.resolution import ConfigHygiene, StrategyConfigError, StrategyConfigResolver
from tests.support.strategies import (
    INSTRUMENT_MASTER,
    REMOVE,
    changed,
    raw_config,
    threshold_registry,
)


def _resolver() -> StrategyConfigResolver:
    return StrategyConfigResolver(threshold_registry(), INSTRUMENT_MASTER)


def _rejected(raw: dict[str, Any]) -> str:
    with pytest.raises(StrategyConfigError) as error:
        _resolver().resolve(raw, source="threshold.yaml")
    return str(error.value)


def test_a_valid_config_resolves_symbols_to_the_ids_that_run() -> None:
    config = _resolver().resolve(raw_config())

    assert config.name == "threshold" and config.enabled and config.timeframe is Timeframe.M5
    assert [(m.symbol, m.instrument_id) for m in config.universe] == [
        ("NSE:ALPHA-EQ", "NSE:1001"),
        ("NSE:BETA-EQ", "NSE:1002"),
    ]
    assert str(config.parameters.model_dump()["threshold"]) == "100"
    assert str(config.risk.stop_loss_pct) == "1.0" and config.session.square_off_at.hour == 15


def test_a_rejection_names_the_file_and_the_field() -> None:
    message = _rejected(changed(raw_config(), "risk.stop_loss_pct", "0"))
    assert message.startswith("threshold.yaml:") and "risk.stop_loss_pct" in message


@pytest.mark.parametrize(
    "path",
    ["name", "enabled", "timeframe", "universe", "risk", "execution", "session",
     "risk.max_position_value", "execution.max_reprices", "session.square_off_at"],
)  # fmt: skip
def test_a_missing_required_field_is_rejected(path: str) -> None:
    assert path.split(".")[-1] in _rejected(changed(raw_config(), path, REMOVE))


def test_unknown_keys_are_rejected_at_every_level() -> None:
    for path in ["extra", "risk.extra", "universe.extra", "session.extra", "execution.extra"]:
        assert "extra" in _rejected(changed(raw_config(), path, 1))


def test_unknown_parameters_are_rejected() -> None:
    assert "threshld" in _rejected(changed(raw_config(), "parameters.threshld", "1"))


def test_wrongly_typed_parameters_are_rejected() -> None:
    message = _rejected(changed(raw_config(), "parameters.quantity", "ten"))
    assert message.startswith("threshold.yaml: parameters: quantity")


def test_parameters_are_optional_only_if_the_strategy_has_no_required_ones() -> None:
    assert "threshold" in _rejected(changed(raw_config(), "parameters", {}))


@pytest.mark.parametrize(
    "path",
    ["risk.stop_loss_pct", "risk.max_position_value", "execution.limit_buffer_bps",
     "parameters.threshold", "parameters.anything_else"],
)  # fmt: skip
def test_a_yaml_float_is_rejected_wherever_it_appears(path: str) -> None:
    message = _rejected(changed(raw_config(), path, 1.5))
    assert "YAML float" in message and path in message


def test_a_float_inside_a_list_is_found_too() -> None:
    assert "universe.instruments[1]" in _rejected(
        changed(raw_config(), "universe.instruments", ["NSE:ALPHA-EQ", 2.5])
    )


@pytest.mark.parametrize(
    "key",
    ["api_key", "apikey", "API-KEY", "password", "passwd", "secret", "totp_secret", "client_code",
     "access_token", "refresh_token", "private_key", "authorization", "bearer", "credentials"],
)  # fmt: skip
def test_credential_shaped_keys_are_rejected_at_any_depth(key: str) -> None:
    for path in [key, f"parameters.{key}", f"risk.{key}", f"universe.{key}"]:
        message = _rejected(changed(raw_config(), path, "x"))
        assert "credential" in message and key in message


def test_a_credential_key_nested_in_a_mapping_inside_a_list_is_found() -> None:
    raw = changed(raw_config(), "parameters.legs", [{"name": "a", "password": "x"}])
    assert "credential" in _rejected(raw)


def test_ordinary_keys_that_merely_look_similar_are_not_flagged() -> None:
    ConfigHygiene().check({"pinned": 1, "author": "x", "keyword": 2})


def test_an_unregistered_strategy_is_rejected_listing_what_exists() -> None:
    message = _rejected(changed(raw_config(), "name", "nonesuch"))
    assert "nonesuch" in message and "threshold" in message


@pytest.mark.parametrize(
    ("symbol", "fragment"),
    [
        ("ALPHA-EQ", "EXCHANGE:TRADINGSYMBOL"),
        ("NSE:", "EXCHANGE:TRADINGSYMBOL"),
        ("XYZ:ALPHA-EQ", "unknown exchange"),
        ("NSE:GAMMA-EQ", "not in the instrument master"),
        ("BSE:ALPHA-EQ", "not in the instrument master"),
    ],
)
def test_a_bad_symbol_is_rejected(symbol: str, fragment: str) -> None:
    raw = changed(raw_config(), "universe.instruments", [symbol])
    assert fragment in _rejected(raw)


def test_a_token_written_where_a_symbol_belongs_does_not_resolve() -> None:
    """Config names instruments by symbol; a raw token is not a symbol."""
    assert "not in the instrument master" in _rejected(
        changed(raw_config(), "universe.instruments", ["NSE:1001"])
    )


def test_an_empty_or_duplicated_universe_is_rejected() -> None:
    assert _rejected(changed(raw_config(), "universe.instruments", []))
    assert "twice" in _rejected(
        changed(raw_config(), "universe.instruments", ["NSE:ALPHA-EQ", "NSE:ALPHA-EQ"])
    )


def test_only_a_static_universe_is_supported() -> None:
    assert "type" in _rejected(changed(raw_config(), "universe.type", "dynamic"))


@pytest.mark.parametrize("value", ["yes", 1, None, "true"])
def test_enabled_must_be_a_real_boolean(value: object) -> None:
    assert "enabled" in _rejected(changed(raw_config(), "enabled", value))


def test_an_unknown_timeframe_is_rejected() -> None:
    assert "timeframe" in _rejected(changed(raw_config(), "timeframe", "7m"))


def test_an_unquoted_time_of_day_is_rejected_not_misread() -> None:
    assert "session.no_new_entries_after" in _rejected(
        changed(raw_config(), "session.no_new_entries_after", 900)
    )

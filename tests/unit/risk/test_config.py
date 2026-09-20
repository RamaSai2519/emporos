"""The limits file: what it accepts and, more importantly, what it refuses."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emporos.core.errors import ConfigurationError
from emporos.risk.config import DEFAULT_RISK_FILE, RiskLimitsLoader

VALID = """
max_daily_loss: "2000"
max_strategy_loss: "1000"
max_position_value: "25000"
max_open_positions: 3
max_capital_deployed: "60000"
max_order_quantity: 500
max_price_deviation_pct: "2"
max_spread_bps: "20"
duplicate_window_seconds: 5
max_orders_per_second: 2
max_orders_per_minute: 60
"""


def load(tmp_path: Path, text: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "risk.yaml"
    path.write_text(text)
    return RiskLimitsLoader(path).load()


def test_the_shipped_file_loads_and_is_a_complete_set_of_limits() -> None:
    limits = RiskLimitsLoader(DEFAULT_RISK_FILE).load()
    assert limits.max_daily_loss == Decimal("2000") and limits.max_open_positions == 3


def test_a_valid_file_loads_exact_decimals(tmp_path: Path) -> None:
    limits = load(tmp_path, VALID.replace('"2"', '"2.5"'))
    assert limits.max_price_deviation_pct == Decimal("2.5")


def test_a_bare_yaml_float_is_refused_because_money_is_never_a_float(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="max_daily_loss"):
        load(tmp_path, VALID.replace('max_daily_loss: "2000"', "max_daily_loss: 2000.5"))


@pytest.mark.parametrize(
    "line",
    ['max_daily_loss: "0"', 'max_daily_loss: "-5"', "max_order_quantity: 0", "max_open_positions: -1",
     'max_price_deviation_pct: "100"', 'max_spread_bps: "0"'],
)  # fmt: skip
def test_a_zero_or_negative_or_absurd_limit_is_refused(tmp_path: Path, line: str) -> None:
    key = line.split(":")[0]
    text = "\n".join(line if row.startswith(key + ":") else row for row in VALID.splitlines())
    with pytest.raises(ConfigurationError, match=key):
        load(tmp_path, text)


def test_a_missing_limit_is_refused_so_none_is_ever_silently_unset(tmp_path: Path) -> None:
    text = "\n".join(r for r in VALID.splitlines() if not r.startswith("max_capital_deployed"))
    with pytest.raises(ConfigurationError, match="max_capital_deployed"):
        load(tmp_path, text)


def test_an_unknown_limit_is_refused_so_a_typo_cannot_hide(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="max_daily_los"):
        load(tmp_path, VALID + 'max_daily_los: "5"\n')


def test_a_missing_file_a_broken_file_and_a_non_mapping_are_configuration_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="cannot read"):
        RiskLimitsLoader(tmp_path / "nope.yaml").load()
    with pytest.raises(ConfigurationError, match="not valid YAML"):
        load(tmp_path, "a: [unclosed")
    with pytest.raises(ConfigurationError, match="mapping"):
        load(tmp_path, "- a\n- b\n")

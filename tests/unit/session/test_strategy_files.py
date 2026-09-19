from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigError, StrategyConfigResolver
from tests.support.strategies import INSTRUMENT_MASTER, raw_config, threshold_registry


def _loader(directory: Path) -> StrategyConfigLoader:
    return StrategyConfigLoader(
        StrategyConfigResolver(threshold_registry(), INSTRUMENT_MASTER), directory
    )


def _write(directory: Path, name: str, raw: dict[str, Any] | str) -> Path:
    path = directory / name
    path.write_text(raw if isinstance(raw, str) else yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_a_valid_file_loads_and_resolves(tmp_path: Path) -> None:
    config = _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", raw_config()))
    assert config.name == "threshold" and config.instrument_ids == ("NSE:1001", "NSE:1002")


def test_the_plan_example_written_the_way_the_plan_writes_it_is_refused_on_its_float(
    tmp_path: Path,
) -> None:
    """plan.md §9 writes `stop_loss_pct: 1.0`; a real YAML float is exactly what we refuse."""
    text = yaml.safe_dump(raw_config()).replace("stop_loss_pct: '1.0'", "stop_loss_pct: 1.0")
    assert "stop_loss_pct: 1.0" in text
    with pytest.raises(StrategyConfigError, match="YAML float"):
        _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", text))


def test_an_unquoted_time_of_day_is_refused_not_read_as_seconds(tmp_path: Path) -> None:
    text = yaml.safe_dump(raw_config()).replace("'15:00'", "15:00")
    assert "no_new_entries_after: 15:00" in text
    with pytest.raises(StrategyConfigError, match="no_new_entries_after"):
        _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", text))


def test_a_repeated_key_is_an_error_not_a_silent_last_one_wins(tmp_path: Path) -> None:
    text = yaml.safe_dump(raw_config()) + "enabled: false\n"
    with pytest.raises(StrategyConfigError, match="duplicate key 'enabled'"):
        _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", text))


def test_a_repeated_key_in_a_nested_mapping_is_an_error_too(tmp_path: Path) -> None:
    text = yaml.safe_dump(raw_config()).replace(
        "max_reprices: 3", "max_reprices: 3\n  max_reprices: 4"
    )
    with pytest.raises(StrategyConfigError, match="duplicate key"):
        _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", text))


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string\n", "", "42\n"])
def test_a_file_that_is_not_a_mapping_is_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(StrategyConfigError, match="YAML mapping"):
        _loader(tmp_path).load_file(_write(tmp_path, "threshold.yaml", text))


def test_broken_yaml_and_missing_files_are_config_errors_naming_the_file(tmp_path: Path) -> None:
    broken = _write(tmp_path, "broken.yaml", "name: [unclosed\n")
    with pytest.raises(StrategyConfigError, match="broken.yaml"):
        _loader(tmp_path).load_file(broken)
    with pytest.raises(StrategyConfigError, match="absent.yaml"):
        _loader(tmp_path).load_file(tmp_path / "absent.yaml")


def test_the_yaml_python_object_tags_are_not_executed(tmp_path: Path) -> None:
    """A config file is data. If a `!!python` tag ran, the directory it names would appear."""
    trace = tmp_path / "executed"
    text = yaml.safe_dump(raw_config()) + f"probe: !!python/object/apply:os.mkdir ['{trace}']\n"

    with pytest.raises(StrategyConfigError):
        _loader(tmp_path).load_file(_write(tmp_path, "evil.yaml", text))

    assert not trace.exists()


def test_load_all_reads_every_yaml_file_in_name_order_and_ignores_the_rest(tmp_path: Path) -> None:
    _write(tmp_path, "b.yaml", raw_config())
    _write(tmp_path, "notes.txt", "not yaml config")
    _write(tmp_path, "c.yml", "ignored: true")

    configs = _loader(tmp_path).load_all()

    assert [c.name for c in configs] == ["threshold"]


def test_load_all_rejects_a_strategy_defined_twice(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", raw_config())
    _write(tmp_path, "b.yaml", raw_config())
    with pytest.raises(StrategyConfigError, match="more than one file"):
        _loader(tmp_path).load_all()


def test_one_bad_file_fails_the_whole_load_loudly(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", raw_config())
    _write(tmp_path, "z.yaml", {"name": "threshold"})
    with pytest.raises(StrategyConfigError, match="z.yaml"):
        _loader(tmp_path).load_all()


def test_load_enabled_returns_only_enabled_strategies_but_validates_all(tmp_path: Path) -> None:
    disabled = raw_config() | {"enabled": False}
    _write(tmp_path, "a.yaml", disabled)
    assert _loader(tmp_path).load_enabled() == []
    assert len(_loader(tmp_path).load_all()) == 1

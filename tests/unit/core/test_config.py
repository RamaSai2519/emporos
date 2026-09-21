from pathlib import Path

import pytest

from emporos.core.config import Environment, Settings, YamlConfigLoader


@pytest.mark.parametrize(
    ("env", "expected_db"),
    [
        (None, "emporos_dev"),
        ("", "emporos_dev"),
        ("local", "emporos_dev"),
        ("dev", "emporos_dev"),
        ("staging", "emporos_dev"),
        ("ci", "emporos_dev"),
        ("main", "emporos"),
    ],
)
def test_environment_db_name_only_main_is_production(env: str | None, expected_db: str) -> None:
    assert Environment(env).db_name == expected_db


@pytest.mark.parametrize(
    ("env", "expected_profile"),
    [
        (None, "local"),
        ("local", "local"),
        ("dev", "local"),
        ("staging", "staging"),
        ("main", "production"),
    ],
)
def test_environment_profile(env: str | None, expected_profile: str) -> None:
    assert Environment(env).profile == expected_profile


@pytest.mark.parametrize("env", [None, "", "local", "dev", "staging", "ci"])
def test_environment_db_name_override_applies_outside_production(env: str | None) -> None:
    assert Environment(env, db_name_override="emporos_smoke").db_name == "emporos_smoke"


def test_environment_db_name_override_ignored_under_production() -> None:
    assert Environment("main", db_name_override="emporos_smoke").db_name == "emporos"


def test_environment_db_name_unset_override_keeps_default() -> None:
    assert Environment("dev", db_name_override=None).db_name == "emporos_dev"


def test_yaml_config_loader_merges_base_and_profile_overlay() -> None:
    config = YamlConfigLoader().load("production")
    assert config["log_level"] == "WARNING"
    assert config["timezone"] == "Asia/Kolkata"  # from base, not overridden


def test_yaml_config_loader_missing_overlay_falls_back_to_base(tmp_path: Path) -> None:
    (tmp_path / "settings.base.yaml").write_text("log_level: INFO\n")
    config = YamlConfigLoader(config_dir=tmp_path).load("nonexistent-profile")
    assert config == {"log_level": "INFO"}


def test_settings_env_unset_defaults_to_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    settings = Settings(_env_file=None)
    assert settings.env is None
    assert settings.db_name == "emporos_dev"
    assert settings.profile == "local"


def test_settings_env_main_resolves_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "main")
    settings = Settings(_env_file=None)
    assert settings.db_name == "emporos"
    assert settings.profile == "production"


def test_settings_reads_mongo_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URL", "mongodb+srv://example/")
    settings = Settings(_env_file=None)
    assert settings.mongo_url == "mongodb+srv://example/"


def test_settings_mongo_db_name_overrides_db_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MONGO_DB_NAME", "emporos_smoke")
    settings = Settings(_env_file=None)
    assert settings.db_name == "emporos_smoke"


def test_settings_mongo_db_name_ignored_under_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "main")
    monkeypatch.setenv("MONGO_DB_NAME", "emporos_smoke")
    settings = Settings(_env_file=None)
    assert settings.db_name == "emporos"


def test_settings_default_is_cached_and_loads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    first = Settings.default()
    second = Settings.default()
    assert first is second
    assert first.profile == "staging"

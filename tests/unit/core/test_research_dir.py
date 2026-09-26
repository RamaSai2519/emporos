"""The durable research directory: an environment override, else a fixed home default."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.core.research_dir import ENV_VAR, research_dir


def test_the_environment_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path))

    assert research_dir() == tmp_path


def test_the_default_is_durable_not_a_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)

    assert research_dir() == Path.home() / ".local" / "share" / "emporos" / "research"
    assert ".cache" not in research_dir().parts

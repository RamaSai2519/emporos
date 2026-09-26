"""EM-244: research data lives outside ~/.cache, and its root is configurable."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.core.paths import RESEARCH_DIR_ENV, research_dir


def test_the_default_is_under_local_share_never_under_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RESEARCH_DIR_ENV, raising=False)

    assert research_dir() == Path.home() / ".local" / "share" / "emporos" / "research"
    assert ".cache" not in research_dir().parts


def test_the_environment_overrides_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RESEARCH_DIR_ENV, str(tmp_path / "r"))

    assert research_dir() == tmp_path / "r"


def test_no_research_default_points_into_the_cache_directory() -> None:
    from emporos.cli import posture_commands
    from emporos.research.filings import attachments, event_store, raw_store, snapshot

    for path in (
        raw_store.DEFAULT_RAW_DIR, attachments.DEFAULT_TEXT_DIR, event_store.DEFAULT_EVENT_DIR,
        snapshot.DEFAULT_SNAPSHOT_DIR, posture_commands.DEFAULT_FO_STOCK_DIR,
    ):  # fmt: skip
        assert ".cache" not in path.parts, path

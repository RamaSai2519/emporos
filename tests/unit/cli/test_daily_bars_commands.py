"""EM-220: the build command's shard argument and where it writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.cli.daily_bars_commands import DERIVED_DIR_NAME, derived_candle_root, parse_shard
from emporos.core.config import Settings


@pytest.mark.parametrize(("text", "expected"), [("1/1", (0, 1)), ("2/4", (1, 4)), ("4/4", (3, 4))])
def test_a_shard_is_a_residue_class_of_the_name_list(text: str, expected: tuple[int, int]) -> None:
    assert parse_shard(text) == expected


@pytest.mark.parametrize("text", ["0/4", "5/4", "a/b", "3", "1/2/3", ""])
def test_a_malformed_shard_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="shard"):
        parse_shard(text)


def test_shards_partition_the_names_exactly() -> None:
    names = [f"NSE:{i}" for i in range(10)]

    parts = [names[residue::total] for residue, total in (parse_shard(f"{n}/3") for n in (1, 2, 3))]

    assert sorted(name for part in parts for name in part) == sorted(names)


def test_derived_bars_go_beside_the_candle_cache_never_into_it(tmp_path: Path) -> None:
    settings = Settings.default().model_copy(update={"candle_cache_dir": str(tmp_path / "candles")})

    root = derived_candle_root(settings)

    assert root == tmp_path / DERIVED_DIR_NAME
    assert root != tmp_path / "candles"

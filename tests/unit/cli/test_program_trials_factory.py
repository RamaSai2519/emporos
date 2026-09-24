"""EM-191 F2B: program-wide N is never built without the historical grids."""

from __future__ import annotations

from pathlib import Path

import pytest
from pymongo import AsyncMongoClient

from emporos.cli.program_trials import ProgramTrialCountFactory
from emporos.core.errors import ConfigurationError


def test_n_is_not_built_when_the_historical_grids_are_missing(tmp_path: Path) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        "mongodb://localhost:1", connect=False
    )
    factory = ProgramTrialCountFactory(historical_grids=tmp_path / "absent.yaml")

    with pytest.raises(ConfigurationError):
        factory.build(client["unused"])

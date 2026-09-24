"""EM-191 §4.3: the vault's committed state is read strictly, fails closed, and the seal shipped in
the repository is the plan's."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.backtest.vault import VaultBurnedError, VaultViolation
from emporos.cli.backtest_parallel import CurationRecipe
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.main import app
from emporos.cli.vault_files import DEFAULT_OPENS_DIR, DEFAULT_SEAL_FILE, VaultFiles
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.risk.config import RiskLimitsLoader

SEAL_TEXT = """\
first_day: 2026-03-19
last_day: 2026-09-18
instruments: all
max_opens: 3
"""

runner = CliRunner()


class Git(GitRepository):
    """Git that says whether a path is committed, without running git."""

    def __init__(self, committed: bool = True) -> None:
        super().__init__()
        self._committed = committed

    def is_committed(self, path: Path) -> bool:
        return self._committed


def files(tmp_path: Path, seal: str = SEAL_TEXT, *, committed: bool = True) -> VaultFiles:
    (tmp_path / "vault.yaml").write_text(seal, encoding="utf-8")
    (tmp_path / "opens").mkdir(exist_ok=True)
    return VaultFiles(tmp_path / "vault.yaml", tmp_path / "opens", Git(committed))


def open_text(seal_hash: str, number: int = 1, candidate: str = "cand-1") -> str:
    return f"""\
open_number: {number}
seal_hash: {seal_hash}
candidate_hash: {candidate}
reason: frozen candidate for S5
opened_at: 2026-10-01T09:00:00+05:30
first_day: 2026-03-19
last_day: 2026-09-18
instruments: all
"""


# --- the seal shipped in this repository ----------------------------------------------------


def test_the_shipped_seal_is_the_plans_and_only_the_operator_may_move_it() -> None:
    """Pinned on purpose (like the S2 bar): shrinking the vault is a commit that edits this test."""
    seal = VaultFiles().load_seal()

    assert (seal.first_day, seal.last_day) == (date(2026, 3, 19), date(2026, 9, 18))
    assert seal.instrument_ids is None and seal.max_opens == 3
    assert seal.content_hash == "b4b21b9e8c0332c59a7de0d45de1bc2d9ef73c12107302a4082ef6353adbe7aa"


def test_the_shipped_files_build_a_gate() -> None:
    gate = VaultFiles(DEFAULT_SEAL_FILE, DEFAULT_OPENS_DIR).load()

    assert gate.opens_used + gate.opens_left == 3


# --- reading ---------------------------------------------------------------------------------


def test_with_no_opens_the_vault_is_sealed_with_three_left(tmp_path: Path) -> None:
    gate = files(tmp_path).load()

    assert (gate.opens_used, gate.opens_left) == (0, 3)


def test_a_committed_open_is_read_and_counted(tmp_path: Path) -> None:
    loader = files(tmp_path)
    seal_hash = loader.load_seal().content_hash
    (tmp_path / "opens" / "open-1.yaml").write_text(open_text(seal_hash), encoding="utf-8")

    gate = loader.load()

    assert (gate.opens_used, gate.opens_left) == (1, 2)
    gate.check(
        "NSE:1", datetime(2026, 5, 4, tzinfo=UTC), datetime(2026, 5, 5, tzinfo=UTC),
        candidate_hash="cand-1",
    )  # fmt: skip


def test_an_open_that_is_not_committed_is_refused_not_ignored(tmp_path: Path) -> None:
    loader = files(tmp_path, committed=False)
    seal_hash = loader.load_seal().content_hash
    (tmp_path / "opens" / "open-1.yaml").write_text(open_text(seal_hash), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not committed"):
        loader.load()


def test_a_fourth_open_file_makes_the_gate_burn(tmp_path: Path) -> None:
    loader = files(tmp_path)
    seal_hash = loader.load_seal().content_hash
    for n in (1, 2, 3, 4):
        (tmp_path / "opens" / f"open-{n}.yaml").write_text(
            open_text(seal_hash, n, f"c{n}"), encoding="utf-8"
        )

    with pytest.raises(VaultBurnedError, match="burned"):
        loader.load()


def test_a_missing_seal_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read"):
        VaultFiles(tmp_path / "nope.yaml", tmp_path, Git()).load()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- a list", "must be a mapping"),
        (SEAL_TEXT + "extra: 1\n", "unknown key"),
        ("first_day: 2026-03-19\n", "missing"),
        (SEAL_TEXT.replace("2026-03-19", "soon"), "must be a date"),
        (SEAL_TEXT.replace("all", "[]"), "instruments must be"),
        (SEAL_TEXT.replace("max_opens: 3", "max_opens: 9"), "1 to 3"),
        (SEAL_TEXT.replace("2026-03-19", "2026-12-01"), "after its last"),
        (": : :", "not valid YAML"),
    ],
)
def test_a_malformed_seal_is_refused(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        files(tmp_path, text).load_seal()


def test_a_list_of_instruments_seals_just_those(tmp_path: Path) -> None:
    seal = files(tmp_path, SEAL_TEXT.replace("all", '["NSE:2885", "NSE:11536"]')).load_seal()

    assert seal.instrument_ids == frozenset({"NSE:2885", "NSE:11536"})


# --- the wiring: a curation worker cannot see the vault --------------------------------------


async def test_a_curation_worker_reads_bars_through_the_vault(tmp_path: Path) -> None:
    recipe = CurationRecipe(
        cache_root=tmp_path / "cache",
        snapshot_root=tmp_path / "snapshot",
        eras=(),
        universe_at=datetime(2026, 1, 1, tzinfo=UTC),
        assume_current_universe=True,
        assume_fees=True,
        limits=RiskLimitsLoader().load(),
        vault=files(tmp_path).load(),
    )

    with pytest.raises(VaultViolation):
        await recipe.candle_reader().get_range(
            "NSE:2885",
            Timeframe.M5,
            datetime(2026, 5, 4, tzinfo=UTC),
            datetime(2026, 5, 5, tzinfo=UTC),
        )
    assert (
        await recipe.candle_reader().get_range(
            "NSE:2885",
            Timeframe.M5,
            datetime(2026, 1, 5, tzinfo=UTC),
            datetime(2026, 1, 6, tzinfo=UTC),
        )
        == []
    )  # history before the vault reads normally (an empty cache here)


# --- the status command ----------------------------------------------------------------------


def test_status_reports_the_shipped_vault() -> None:
    result = runner.invoke(app, ["research", "vault", "status"])

    assert result.exit_code == 0, result.output
    assert "sealed: 2026-03-19 to 2026-09-18" in result.output
    assert "opens used: 0 of 3 (3 left)" in result.output

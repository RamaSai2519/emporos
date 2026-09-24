"""EM-191 F2: program-wide N, and the guarantee that no Deflated Sharpe is priced at a local N."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path
from typing import get_type_hints

import pytest

from emporos.backtest.robustness.assessment import RobustnessAssessor
from emporos.backtest.robustness.program_trials import (
    LedgerTrialCounter,
    ProgramTrialCount,
    RegistryIndexCounter,
    StoreTrialCounter,
)
from emporos.backtest.robustness.trials import InMemoryTrialLedger
from emporos.core.errors import ConfigurationError
from tests.support.architecture import class_uses
from tests.support.program_trials import FixedTrialCounter, program_trials
from tests.unit.backtest.robustness.test_trials import trial


async def ledger_of(*sharpes: str | None) -> InMemoryTrialLedger:
    ledger = InMemoryTrialLedger()
    for n, sharpe in enumerate(sharpes):
        await ledger.append(trial(n, sharpe))
    return ledger


class Counting:
    async def count(self) -> int:
        return 7


# --- counting -------------------------------------------------------------------------------------


async def test_n_sums_every_source_and_only_scored_trials_inform_the_spread() -> None:
    strategy = await ledger_of("0.1", "0.3", None)
    count = ProgramTrialCount(
        {"strategy trials": strategy},
        [FixedTrialCounter("feature trials", 40), StoreTrialCounter("lead-lag", Counting())],
    )

    program = await count.trials()

    assert program.count == 3 + 40 + 7
    assert program.by_source == {"strategy trials": 3, "feature trials": 40, "lead-lag": 7}
    assert program.statistics.scored == 2
    assert program.statistics.sharpe_variance == Decimal("0.02")
    assert (await count.statistics()) == program.statistics


async def test_an_unrecorded_run_still_counts() -> None:
    count = ProgramTrialCount(
        {"strategy trials": await ledger_of("0.1"), "this run": await ledger_of("0.2", "0.4")}
    )

    assert (await count.statistics()).count == 3


async def test_a_ledger_counter_counts_by_listing() -> None:
    assert await LedgerTrialCounter("run", await ledger_of(None, None)).count() == 2


def test_program_n_needs_the_strategy_ledger() -> None:
    with pytest.raises(ValueError, match="at least the strategy"):
        ProgramTrialCount({})


def test_each_source_is_counted_once() -> None:
    with pytest.raises(ValueError, match="counted once"):
        ProgramTrialCount({"x": InMemoryTrialLedger()}, [FixedTrialCounter("x", 1)])


# --- the experiment registry ----------------------------------------------------------------------


async def test_the_registry_counts_one_look_per_published_experiment(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"experiments": [{"id": "a"}, {"id": "b"}]}), encoding="utf-8")

    counter = RegistryIndexCounter(index)

    assert counter.name == "experiment registry"
    assert await counter.count() == 2


async def test_no_registry_yet_counts_zero(tmp_path: Path) -> None:
    assert await RegistryIndexCounter(tmp_path / "index.json").count() == 0


@pytest.mark.parametrize("text", ["{not json", "[]", '{"experiments": 3}'])
async def test_a_malformed_registry_is_refused_not_counted_as_zero(
    tmp_path: Path, text: str
) -> None:
    index = tmp_path / "index.json"
    index.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        await RegistryIndexCounter(index).count()


async def test_the_committed_registry_is_readable() -> None:
    index = Path(__file__).resolve().parents[4] / "docs/strategies/experiments/index.json"

    assert await RegistryIndexCounter(index).count() > 0


# --- no local N -----------------------------------------------------------------------------------


def test_the_assessor_takes_program_wide_n_by_type() -> None:
    parameter = inspect.signature(RobustnessAssessor).parameters["trials"]

    assert get_type_hints(RobustnessAssessor.__init__)[parameter.name] is ProgramTrialCount


# Where a TrialStatistics may be built. Everything else must take a ProgramTrialCount.
TRIAL_STATISTICS_BUILDERS = {
    "emporos/backtest/robustness/trials.py",  # its own definition
    "emporos/backtest/robustness/program_trials.py",  # the one program-wide producer
    "emporos/cli/trial_commands.py",  # `trials list`: a per-ledger display, prices nothing
    # EM-187's Jev arm comparison prices its arms at the strategy ledger's count. It is not an
    # S4/S5 call; L16 must move it onto ProgramTrialCount before any Jev result feeds either.
    "emporos/backtest/jev_sweep.py",
}
DEFLATED_SHARPE_CALLERS = {
    "emporos/backtest/robustness/assessment.py",  # S4, via ProgramTrialCount
    "emporos/backtest/jev_incremental.py",  # given its statistics by jev_sweep (see above)
}


def test_no_module_outside_the_allowlist_builds_its_own_trial_statistics() -> None:
    offenders = {hit.path for hit in class_uses("TrialStatistics")} - TRIAL_STATISTICS_BUILDERS

    assert not offenders, f"price the DSR through ProgramTrialCount: {sorted(offenders)}"


def test_no_module_outside_the_allowlist_prices_a_deflated_sharpe() -> None:
    offenders = {hit.path for hit in class_uses("DeflatedSharpe")} - DEFLATED_SHARPE_CALLERS

    assert (
        not offenders
    ), f"only S4 (and the pinned Jev comparison) price a DSR: {sorted(offenders)}"


def test_the_scanner_catches_a_planted_local_count(tmp_path: Path) -> None:
    planted = tmp_path / "emporos" / "research" / "vault.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "from x import TrialStatistics\n"
        "def n(): return TrialStatistics(3, 0, None)\n"
        "def m(ts): return TrialStatistics.of(ts)\n",
        encoding="utf-8",
    )

    hits = class_uses("TrialStatistics", root=tmp_path)

    assert [(h.path, h.line) for h in hits] == [
        ("emporos/research/vault.py", 2),
        ("emporos/research/vault.py", 3),
    ]


def test_the_test_helper_is_a_program_count() -> None:
    assert isinstance(program_trials(), ProgramTrialCount)

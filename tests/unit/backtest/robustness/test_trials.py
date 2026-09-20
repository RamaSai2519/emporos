"""The ledger appends and never rewrites; its statistics say how many times we looked."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.robustness.trials import InMemoryTrialLedger, TrialStatistics
from emporos.domain.experiments import DuplicateTrialError, Trial, TrialRole

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def trial(n: int, sharpe: str | None = None, **overrides: object) -> Trial:
    values: dict[str, object] = {
        "trial_id": f"t{n}", "experiment": "exp", "strategy": "s", "candidate": f"c{n}",
        "role": TrialRole.TRAIN, "dataset_version": "d1", "config_hash": "h",
        "cost_model": "fees-v1", "recorded_at": T0 + timedelta(minutes=n),
        "daily_sharpe": Decimal(sharpe) if sharpe else None,
    }  # fmt: skip
    values.update(overrides)
    return Trial(**values)  # type: ignore[arg-type]


class TestLedger:
    async def test_lists_oldest_first(self) -> None:
        ledger = InMemoryTrialLedger()
        for n in (3, 1, 2):
            await ledger.append(trial(n))

        assert [t.trial_id for t in await ledger.all()] == ["t1", "t2", "t3"]

    async def test_an_id_is_never_overwritten(self) -> None:
        ledger = InMemoryTrialLedger()
        await ledger.append(trial(1, "0.1"))

        with pytest.raises(DuplicateTrialError):
            await ledger.append(trial(1, "0.9"))

        assert (await ledger.all())[0].daily_sharpe == Decimal("0.1")

    def test_the_protocol_offers_no_way_to_edit_or_remove(self) -> None:
        from emporos.backtest.robustness.trials import TrialLedger

        public = {name for name in dir(TrialLedger) if not name.startswith("_")}

        assert public == {"append", "all"}


class TestTrialValues:
    def test_needs_an_id_and_an_aware_time_and_a_sane_count(self) -> None:
        with pytest.raises(ValueError, match="id"):
            trial(1, trial_id="")
        with pytest.raises(ValueError, match="timezone"):
            trial(1, recorded_at=datetime(2026, 1, 1))
        with pytest.raises(ValueError, match="negative"):
            trial(1, trade_count=-1)


class TestStatistics:
    def test_counts_every_trial_and_measures_the_spread_of_those_scored(self) -> None:
        result = TrialStatistics.of([trial(1, "0.1"), trial(2, "0.3"), trial(3), trial(4)])

        assert (result.count, result.scored) == (4, 2)
        assert result.sharpe_variance == Decimal("0.02")  # sample variance of 0.1 and 0.3

    def test_no_spread_below_two_scored_trials(self) -> None:
        assert TrialStatistics.of([trial(1, "0.1"), trial(2)]).sharpe_variance is None
        assert TrialStatistics.of([]) == TrialStatistics(0, 0, None)

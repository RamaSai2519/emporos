"""The id of an experiment is a function of its declaration's content, nothing else."""

from dataclasses import replace
from datetime import UTC, datetime

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily


def declaration() -> ExperimentDeclaration:
    return ExperimentDeclaration(
        family=ExperimentFamily.STRATEGY,
        slug="orb-v2",
        hypothesis="Opening-range breakouts continue on high-volume days.",
        economic_rationale="Order-flow imbalance at the open persists for the first hour.",
        falsification="Net expectancy after costs is not positive out of sample.",
        parameter_grid={"range_minutes": ("15", "30")},
        feature_versions={"opening_range": "1"},
        declared_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
    )


class TestExperimentIdMinter:
    def test_the_same_declaration_gets_the_same_id(self) -> None:
        assert ExperimentIdMinter().mint(declaration()) == ExperimentIdMinter().mint(declaration())

    def test_a_one_character_change_gets_a_different_id(self) -> None:
        changed = replace(
            declaration(), hypothesis="Opening-range breakouts continue on high-volume day."
        )

        assert ExperimentIdMinter().mint(changed) != ExperimentIdMinter().mint(declaration())

    def test_the_id_carries_the_declared_date_and_slug(self) -> None:
        assert str(ExperimentIdMinter().mint(declaration())).startswith("EXP-20260924-orb-v2-")

    def test_the_order_parameters_are_declared_in_does_not_change_the_id(self) -> None:
        a = replace(declaration(), parameter_grid={"a": ("1",), "b": ("2",)})
        b = replace(declaration(), parameter_grid={"b": ("2",), "a": ("1",)})

        assert ExperimentIdMinter().mint(a) == ExperimentIdMinter().mint(b)

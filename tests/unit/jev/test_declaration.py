from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily
from emporos.jev.config import JevConfig
from emporos.jev.declaration import JevExperimentDeclaration, ModelCutoffDeclaration
from emporos.jev.leakage import JevLeakageError
from tests.support.experiment_reports import sample_declaration


def _declaration(**grid: tuple[str, ...]) -> ExperimentDeclaration:
    base = sample_declaration("jev-x", ExperimentFamily.JEV_INCREMENTAL)
    full = {
        "model": ("vendor/model-a",),
        "model_knowledge_cutoff": ("2025-03-01",),
        "cutoff_source": ("vendor model card, checked 2026-09-24",),
    }
    full.update(grid)
    return replace(base, parameter_grid={k: v for k, v in full.items() if v})


def test_a_complete_declaration_yields_model_cutoff_and_source() -> None:
    declared = ModelCutoffDeclaration.from_declaration(_declaration())

    assert declared.model == "vendor/model-a"
    assert declared.knowledge_cutoff == date(2025, 3, 1)
    assert "model card" in declared.source


@pytest.mark.parametrize("missing", ["model", "model_knowledge_cutoff", "cutoff_source"])
def test_each_declared_field_is_required(missing: str) -> None:
    with pytest.raises(JevLeakageError, match=missing):
        ModelCutoffDeclaration.from_declaration(_declaration(**{missing: ()}))


def test_more_than_one_cutoff_is_ambiguous_and_refused() -> None:
    with pytest.raises(JevLeakageError, match="exactly one"):
        ModelCutoffDeclaration.from_declaration(
            _declaration(model_knowledge_cutoff=("2025-03-01", "2025-04-01"))
        )


def test_a_cutoff_that_is_not_a_date_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="ISO date"):
        ModelCutoffDeclaration.from_declaration(
            _declaration(model_knowledge_cutoff=("early 2025",))
        )


def test_a_blank_source_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="source"):
        ModelCutoffDeclaration.from_declaration(_declaration(cutoff_source=(" ",)))


def test_a_non_jev_declaration_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="not a Jev one"):
        ModelCutoffDeclaration.from_declaration(sample_declaration())


def test_it_applies_the_declared_model_and_cutoff_to_a_config() -> None:
    declared = ModelCutoffDeclaration.from_declaration(_declaration())

    config = declared.apply_to(JevConfig(enabled=True, fail_open=True))

    assert config.model == "vendor/model-a"
    assert config.model_knowledge_cutoff == date(2025, 3, 1)
    assert config.fail_open is True


def _jev(**overrides: tuple[str, ...]) -> ExperimentDeclaration:
    grid: dict[str, tuple[str, ...]] = {
        "inr_per_1k_tokens": ("0.05",),
        "prompt_version": ("v1",),
        "prompt_hash": ("abc",),
        "mode": ("confirmation", "ranking"),
        "threshold": ("0.5", "0.6"),
    }
    grid.update(overrides)
    return _declaration(**grid)


def test_the_whole_jev_declaration_is_read() -> None:
    declared = JevExperimentDeclaration.from_declaration(_jev())

    assert declared.inr_per_1k_tokens == Decimal("0.05")
    assert declared.modes == ("confirmation", "ranking")
    assert declared.thresholds == (Decimal("0.5"), Decimal("0.6"))
    assert (declared.prompt_version, declared.prompt_hash) == ("v1", "abc")


@pytest.mark.parametrize(
    "missing", ["inr_per_1k_tokens", "prompt_version", "prompt_hash", "mode", "threshold"]
)
def test_every_jev_field_is_required(missing: str) -> None:
    with pytest.raises(JevLeakageError, match=missing):
        JevExperimentDeclaration.from_declaration(_jev(**{missing: ()}))


def test_a_price_or_threshold_that_is_not_sensible_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="cannot be negative"):
        JevExperimentDeclaration.from_declaration(_jev(inr_per_1k_tokens=("-1",)))
    with pytest.raises(JevLeakageError, match="must be a number"):
        JevExperimentDeclaration.from_declaration(_jev(inr_per_1k_tokens=("cheap",)))
    with pytest.raises(JevLeakageError, match="between 0 and 1"):
        JevExperimentDeclaration.from_declaration(_jev(threshold=("1.5",)))


def test_a_selection_may_only_narrow_the_declared_grid() -> None:
    declared = JevExperimentDeclaration.from_declaration(_jev())

    assert declared.select(None, None) == (declared.modes, declared.thresholds)
    assert declared.select(["ranking"], [Decimal("0.6")]) == (("ranking",), (Decimal("0.6"),))
    with pytest.raises(JevLeakageError, match="not in the declared grid"):
        declared.select(["strategy_selection"], None)
    with pytest.raises(JevLeakageError, match="not in the declared grid"):
        declared.select(None, [Decimal("0.9")])

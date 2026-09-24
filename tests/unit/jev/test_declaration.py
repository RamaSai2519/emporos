from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily
from emporos.jev.config import JevConfig
from emporos.jev.declaration import ModelCutoffDeclaration
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

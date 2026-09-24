"""EM-188: a declaration is read strictly, and the shipped ones are valid."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    ExperimentDeclarationLoader,
)
from emporos.core.errors import ConfigurationError
from emporos.domain.research_experiments import ExperimentFamily

VALID = """\
family: strategy
slug: orb-test
hypothesis: breakouts continue
economic_rationale: order flow persists
falsification: net expectancy is not positive
parameter_grid:
  range_bars: [3, 6]
  target_r: ["1.5", "3.0"]
feature_versions:
  opening_range: 1
declared_at: 2026-09-24T09:00:00+05:30
"""


def write(tmp_path: Path, text: str, name: str = "orb-test") -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoad:
    def test_a_valid_declaration_is_read_field_for_field(self, tmp_path: Path) -> None:
        declared = ExperimentDeclarationLoader().load(write(tmp_path, VALID))

        assert declared.family is ExperimentFamily.STRATEGY
        assert declared.slug == "orb-test"
        assert dict(declared.parameter_grid) == {
            "range_bars": ("3", "6"),
            "target_r": ("1.5", "3.0"),
        }
        assert dict(declared.feature_versions) == {"opening_range": "1"}
        assert declared.declared_at == datetime(2026, 9, 24, 3, 30, tzinfo=UTC)

    def test_the_grid_and_versions_are_optional(self, tmp_path: Path) -> None:
        text = "\n".join(
            line
            for line in VALID.splitlines()
            if not line.startswith((" ", "parameter", "feature"))
        )

        declared = ExperimentDeclarationLoader().load(write(tmp_path, text))

        assert dict(declared.parameter_grid) == {} and dict(declared.feature_versions) == {}

    @pytest.mark.parametrize(
        ("edit", "message"),
        [
            (lambda t: t + "extra: 1\n", "unknown key"),
            (lambda t: t.replace("hypothesis: breakouts continue\n", ""), "missing"),
            (lambda t: t.replace("[3, 6]", "[3, 1.5]"), "quoted string"),
            (lambda t: t.replace("[3, 6]", "[true]"), "quoted string"),
            (lambda t: t.replace("[3, 6]", "[]"), "non-empty"),
            (lambda t: t.replace("opening_range: 1", "opening_range: 1.5"), "quoted string"),
            (lambda t: t.replace("family: strategy", "family: astrology"), "family"),
            (lambda t: t.replace("+05:30", ""), "UTC offset"),
            (lambda t: t.replace("2026-09-24T09:00:00+05:30", "soon"), "ISO-8601"),
            (lambda t: t.replace("slug: orb-test", "slug: other"), "file's name"),
            (
                lambda t: t.replace(
                    "economic_rationale: order flow persists", "economic_rationale: ' '"
                ),
                "rationale",
            ),
        ],
    )
    def test_a_bad_declaration_is_refused(self, tmp_path: Path, edit: object, message: str) -> None:
        text = edit(VALID)  # type: ignore[operator]

        with pytest.raises(ConfigurationError, match=message):
            ExperimentDeclarationLoader().load(write(tmp_path, text))

    def test_a_file_that_is_not_a_mapping_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="mapping"):
            ExperimentDeclarationLoader().load(write(tmp_path, "- just\n- a list\n"))

    def test_invalid_yaml_and_a_missing_file_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="valid YAML"):
            ExperimentDeclarationLoader().load(write(tmp_path, "a: [unclosed"))
        with pytest.raises(ConfigurationError, match="cannot read"):
            ExperimentDeclarationLoader().load(tmp_path / "absent.yaml")


def test_every_shipped_declaration_is_valid() -> None:
    files = sorted(DEFAULT_DECLARATIONS_DIR.glob("*.yaml"))

    assert files, "the repository ships at least one declaration"
    for path in files:
        assert ExperimentDeclarationLoader().load(path).slug == path.stem

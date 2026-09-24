"""EM-187: `emporos backtest jev-compare` refuses, before it opens a database or spends a rupee,
everything the experiment's guardrails forbid."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.cli.experiment_declarations import ExperimentDeclarationLoader
from emporos.cli.jev_commands import (
    DEFAULT_MAX_REQUESTS,
    JevCompareRequest,
    JevCompareRun,
    ProviderMode,
    SpendConfirmation,
)
from emporos.cli.main import app
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.jev.budget import CallBudgetJevProvider, JevBudgetExceeded, JevSpendCeiling
from emporos.jev.config import JevConfig, JevCredentialsMissing
from emporos.jev.declaration import JevExperimentDeclaration
from emporos.jev.prompts import DEFAULT_PROMPT
from tests.support.jev import RecordingProvider, make_jev_request

runner = CliRunner()
SHIPPED = Path("config/experiments/jev_incremental_v1.yaml")


def _declaration_text(**overrides: str) -> str:
    grid = {
        "model": '["vendor/model-a"]',
        "model_knowledge_cutoff": '["2023-10-01"]',
        "cutoff_source": '["vendor model card, checked 2026-09-24"]',
        "inr_per_1k_tokens": '["0.05"]',
        "prompt_version": '["v1"]',
        "prompt_hash": f'["{DEFAULT_PROMPT.content_hash}"]',
        "mode": '["confirmation", "ranking"]',
        "threshold": '["0.5", "0.6"]',
    }
    grid.update(overrides)
    lines = "\n".join(f"  {k}: {v}" for k, v in grid.items() if v)
    return (
        "family: jev_incremental\nslug: jev-cli-test\nhypothesis: h\neconomic_rationale: r\n"
        f"falsification: f\nparameter_grid:\n{lines}\ndeclared_at: 2026-09-24T09:00:00+05:30\n"
    )


def _write(tmp_path: Path, **overrides: str) -> Path:
    path = tmp_path / "jev-cli-test.yaml"
    path.write_text(_declaration_text(**overrides), encoding="utf-8")
    return path


def _invoke(path: Path, *extra: str, first: str = "2024-06-03", last: str = "2024-09-30"):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app,
        [
            "backtest", "jev-compare", "--declaration", str(path),
            "--from", first, "--to", last, "--allow-uncommitted-declaration", *extra,
        ],
    )  # fmt: skip


def test_the_command_is_registered_and_documented() -> None:
    result = runner.invoke(app, ["backtest", "jev-compare", "--help"], terminal_width=200)

    assert result.exit_code == 0
    for option in ("--provider", "--threshold", "--anonymise", "--max-requests", "--declaration"):
        assert option in result.output


def test_the_shipped_declaration_is_valid_and_states_its_cutoff_source() -> None:
    declared = ExperimentDeclarationLoader().load(SHIPPED)

    jev = JevExperimentDeclaration.from_declaration(declared)

    assert jev.model.knowledge_cutoff == date(2023, 10, 1)
    assert "developers.openai.com" in jev.model.source
    assert jev.prompt_hash == DEFAULT_PROMPT.content_hash


def test_an_uncommitted_declaration_is_refused_without_the_explicit_override(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["backtest", "jev-compare", "--declaration", str(_write(tmp_path)),
         "--from", "2024-06-03", "--to", "2024-09-30"],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "not committed" in result.output


def test_a_declaration_without_a_cutoff_is_refused(tmp_path: Path) -> None:
    result = _invoke(_write(tmp_path, model_knowledge_cutoff=""))

    assert result.exit_code == 1
    assert "model_knowledge_cutoff" in result.output


def test_a_declaration_without_a_cutoff_source_is_refused(tmp_path: Path) -> None:
    result = _invoke(_write(tmp_path, cutoff_source=""))

    assert result.exit_code == 1
    assert "cutoff_source" in result.output


def test_a_window_the_model_may_remember_is_refused(tmp_path: Path) -> None:
    result = _invoke(_write(tmp_path), first="2023-11-01", last="2024-01-31")

    assert result.exit_code == 1
    assert "may remember" in result.output


def test_a_prompt_other_than_the_declared_one_is_refused(tmp_path: Path) -> None:
    result = _invoke(_write(tmp_path, prompt_hash='["deadbeef"]'))

    assert result.exit_code == 1
    assert "declare the prompt actually used" in result.output


def test_a_mode_or_threshold_outside_the_declared_grid_is_refused(tmp_path: Path) -> None:
    by_mode = _invoke(_write(tmp_path), "--mode", "strategy_selection")
    by_threshold = _invoke(_write(tmp_path), "--threshold", "0.9")

    assert by_mode.exit_code == 1 and "not in the declared grid" in by_mode.output
    assert by_threshold.exit_code == 1 and "not in the declared grid" in by_threshold.output


def test_a_non_jev_declaration_is_refused(tmp_path: Path) -> None:
    text = _declaration_text().replace("family: jev_incremental", "family: strategy")
    path = tmp_path / "jev-cli-test.yaml"
    path.write_text(text, encoding="utf-8")

    result = _invoke(path)

    assert result.exit_code == 1
    assert "not a Jev one" in result.output


def _request(provider: ProviderMode, *, yes: bool = False) -> JevCompareRequest:
    declared = ExperimentDeclarationLoader().load(SHIPPED)
    return JevCompareRequest(
        declaration=declared, plan=Path("p"), first=date(2024, 6, 3), last=date(2024, 9, 30),
        modes=None, thresholds=None, provider=provider, anonymise=True,
        max_requests=DEFAULT_MAX_REQUESTS, assume_yes=yes, benchmark=Path("b"),
        experiments_dir=Path("e"), assume_earliest_fees=False, assume_current_universe=False,
        record_trials=False,
    )  # fmt: skip


def _settings(key: str | None) -> Settings:
    return Settings.default().model_copy(update={"vercel_gateway_key": key})


def _jev() -> JevExperimentDeclaration:
    return JevExperimentDeclaration.from_declaration(ExperimentDeclarationLoader().load(SHIPPED))


class TestRecordMode:
    def test_replay_needs_no_live_provider(self) -> None:
        run = JevCompareRun(_settings(None))

        live = run._live_provider(JevConfig(), _request(ProviderMode.REPLAY), _jev())

        assert live is None

    def test_record_without_a_gateway_key_is_refused(self) -> None:
        run = JevCompareRun(_settings(None))

        with pytest.raises(JevCredentialsMissing):
            run._live_provider(JevConfig(), _request(ProviderMode.RECORD, yes=True), _jev())

    def test_record_that_the_operator_declines_spends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
        run = JevCompareRun(_settings("vck_test"))

        with pytest.raises(EmporosError, match="nothing was spent"):
            run._live_provider(JevConfig(), _request(ProviderMode.RECORD), _jev())

    def test_a_confirmed_record_run_is_capped(self) -> None:
        run = JevCompareRun(_settings("vck_test"))

        live = run._live_provider(JevConfig(), _request(ProviderMode.RECORD, yes=True), _jev())

        assert isinstance(live, CallBudgetJevProvider)


class TestSpend:
    def test_the_ceiling_is_calls_times_tokens_times_the_rate(self) -> None:
        ceiling = JevSpendCeiling(500, 400, Decimal("0.05"))

        assert ceiling.tokens == 200_000
        assert ceiling.inr == Decimal("10")

    def test_the_disclosure_states_the_ceiling_before_asking(self, capsys) -> None:  # type: ignore[no-untyped-def]
        confirmation = SpendConfirmation(JevSpendCeiling(500, 400, Decimal("0.05")), True)

        assert confirmation.confirm() is True
        out = capsys.readouterr().out
        assert "at most 500" in out and "200000 tokens" in out and "10.00 INR" in out

    async def test_the_budget_stops_the_run_rather_than_degrading_it(self) -> None:
        inner = RecordingProvider()
        budget = CallBudgetJevProvider(inner, 2)

        await budget.decide(make_jev_request())
        await budget.decide(make_jev_request())
        with pytest.raises(JevBudgetExceeded, match="cap of 2"):
            await budget.decide(make_jev_request())

        assert len(inner.requests) == 2 and budget.used == 2

    def test_a_zero_budget_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            CallBudgetJevProvider(RecordingProvider(), 0)

"""The Dev autorun's steps against a scripted command runner (no process, no call)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from emporos.cli.autorun_commands import (
    ExtractionStep,
    FreezeStep,
    SmokeStep,
    TableStep,
    VariantStep,
    build_steps,
)
from emporos.eventtrader.autorun import Outcome
from emporos.research.filings.snapshot import RECORD


class ScriptedRunner:
    def __init__(self, code: int = 0, tail: str = "ok") -> None:
        self.code, self.tail = code, tail
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, name: str, arguments: Sequence[str]) -> tuple[int, str]:
        self.calls.append((name, list(arguments)))
        return self.code, self.tail


class Probe:
    def __init__(self, running: bool) -> None:
        self._running = running

    def running(self) -> bool:
        return self._running


class ProgressRunner(ScriptedRunner):
    """`extraction-progress` says pending (3) until the slice has been extracted."""

    def __init__(self, finishes: bool) -> None:
        super().__init__()
        self._finishes = finishes

    def run(self, name: str, arguments: Sequence[str]) -> tuple[int, str]:
        super().run(name, arguments)
        if arguments[0] == "extraction-progress":
            extracted = any(c[1][0] == "extract-filing-text" for c in self.calls)
            return (0, "DONE") if extracted and self._finishes else (3, "PENDING: 5 of 9")
        return 0, "ok"


def test_extraction_waits_for_a_running_extractor_and_never_starts_a_second() -> None:
    runner = ProgressRunner(finishes=True)
    result = ExtractionStep(runner, Probe(True)).run()

    assert result.outcome is Outcome.WAIT and "PENDING" in result.detail
    assert all(c[1][0] == "extraction-progress" for c in runner.calls)


def test_extraction_does_a_slice_itself_when_no_extractor_is_alive() -> None:
    partial = ProgressRunner(finishes=False)
    finished = ProgressRunner(finishes=True)

    assert ExtractionStep(partial, Probe(False)).run().outcome is Outcome.WAIT
    assert ("extract-filing-text", ["extract-filing-text", "--limit", "540"]) in partial.calls
    assert ExtractionStep(finished, Probe(False)).run().outcome is Outcome.DONE


def test_extraction_fails_on_an_unexpected_progress_exit() -> None:
    assert ExtractionStep(ScriptedRunner(1, "x"), Probe(False)).run().outcome is Outcome.FAILED
    assert ExtractionStep(ScriptedRunner(0, "DONE"), Probe(False)).run().outcome is Outcome.DONE


def test_freeze_runs_once_and_never_again_once_frozen(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    step = FreezeStep(runner, tmp_path)
    assert step.run().outcome is Outcome.DONE and runner.calls[0][1][:2] == [
        "freeze-events", "dev-2024-v1",
    ]  # fmt: skip

    (tmp_path / "dev-2024-v1").mkdir()
    (tmp_path / "dev-2024-v1" / RECORD).write_text("{}")
    again = ScriptedRunner()
    assert FreezeStep(again, tmp_path).run().outcome is Outcome.DONE
    assert again.calls == []


def test_a_failed_freeze_or_smoke_is_a_failure_with_the_reason(tmp_path: Path) -> None:
    bad = ScriptedRunner(1, "boom")

    assert FreezeStep(bad, tmp_path).run().outcome is Outcome.FAILED
    smoke = SmokeStep(bad, tmp_path, tmp_path / "j").run()
    assert smoke.outcome is Outcome.FAILED and "boom" in smoke.detail


def test_a_variant_is_done_with_a_headline_incomplete_on_exit_3_failed_otherwise(
    tmp_path: Path,
) -> None:
    summary = {
        "trades": 41, "net_benchmark": "1200", "net_adverse": "-300", "control_p": 0.04,
        "fresh_calls": 900, "journal_hits": 12,
    }  # fmt: skip
    (tmp_path / "v1_t60-summary.json").write_text(json.dumps(summary))

    def step(code: int) -> VariantStep:
        return VariantStep("v1_t60", ScriptedRunner(code), tmp_path, "s", tmp_path / "j", tmp_path)

    done = step(0).run()
    assert done.outcome is Outcome.DONE and "trades 41" in done.detail
    assert step(3).run().outcome is Outcome.INCOMPLETE
    assert step(1).run().outcome is Outcome.FAILED


def test_the_table_step_and_the_step_order_never_include_the_arbiter(tmp_path: Path) -> None:
    assert TableStep(ScriptedRunner(), tmp_path).run().outcome is Outcome.DONE
    assert TableStep(ScriptedRunner(2), tmp_path).run().outcome is Outcome.FAILED
    names = [
        s.name
        for s in build_steps(tmp_path, tmp_path, tmp_path / "j", tmp_path, tmp_path, Probe(True))
    ]

    assert names == [
        "extraction", "freeze-events", "smoke", "v1_t60", "v2_t75", "v3_nopanel_t60",
        "v4_posture_t60", "v5_posture_t75", "table",
    ]  # fmt: skip

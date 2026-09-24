"""The pieces behind `emporos backtest verdicts`: what a verdict is bound to, what it says about
sizing, and that recording twice records once."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from emporos.backtest.robustness.benchmark import BenchmarkLoader, BenchmarkScaler
from emporos.cli.verdict_commands import VerdictRecorder, VerdictSubjects
from emporos.core.ids import IdGenerator
from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict
from tests.unit.session.test_shipped_strategy_configs import MASTER

ORB = Path("config/strategies/orb_v1.yaml")


def subjects() -> VerdictSubjects:
    return VerdictSubjects(BenchmarkScaler(BenchmarkLoader().load()), MASTER)


def copy_with(tmp_path: Path, **changes: object) -> Path:
    document = yaml.safe_load(ORB.read_text())
    document.update(changes)
    path = tmp_path / "orb_v1.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


class TestWhatAVerdictIsBoundTo:
    def test_enabling_the_strategy_keeps_the_hash(self, tmp_path: Path) -> None:
        off, _ = subjects().hash_and_notes(copy_with(tmp_path, enabled=False))
        on, _ = subjects().hash_and_notes(copy_with(tmp_path, enabled=True))

        assert off == on and off.startswith("sha256:")

    def test_editing_the_parameters_changes_it(self, tmp_path: Path) -> None:
        before, _ = subjects().hash_and_notes(ORB)
        edited = yaml.safe_load(ORB.read_text())
        edited["parameters"]["range_bars"] = 9
        path = tmp_path / "orb_v1.yaml"
        path.write_text(yaml.safe_dump(edited))

        after, _ = subjects().hash_and_notes(path)

        assert after != before

    def test_a_config_sized_differently_from_the_benchmark_says_so(self) -> None:
        """orb_v1.yaml sizes positions at 25,000; the benchmark judges at 10% of 1,00,000."""
        _, notes = subjects().hash_and_notes(ORB)

        assert len(notes) == 1
        assert "₹10000" in notes[0] and "₹25000" in notes[0]


class Book:
    def __init__(self) -> None:
        self.written: list[tuple[RecordedVerdict, str]] = []

    async def append(self, verdict: RecordedVerdict, verdict_id: str) -> None:
        self.written.append((verdict, verdict_id))

    async def latest(self, strategy: str) -> RecordedVerdict | None:
        mine = [v for v, _ in self.written if v.strategy == strategy]
        return mine[-1] if mine else None


def verdict(outcome: Verdict = Verdict.REJECTED, experiment: str = "em118") -> RecordedVerdict:
    return RecordedVerdict(
        strategy="orb_v1",
        behaviour_hash="sha256:abc",
        verdict=outcome,
        gates=(GateFinding("profit after costs is real", "fail", "net -3136"),),
        capital="50000",
        first_day="2025-09-22",
        last_day="2026-09-18",
        experiment=experiment,
        source="curation",
        recorded_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


class TestRecording:
    async def test_the_same_finding_twice_is_recorded_once(self) -> None:
        book = Book()
        recorder = VerdictRecorder(book, IdGenerator())

        first = await recorder.record([verdict()])
        again = await recorder.record([verdict()])

        assert len(first) == 1 and again == []
        assert len(book.written) == 1

    async def test_a_new_finding_is_appended_and_the_old_one_is_kept(self) -> None:
        book = Book()
        recorder = VerdictRecorder(book, IdGenerator())
        await recorder.record([verdict(Verdict.REJECTED)])

        await recorder.record([verdict(Verdict.INCONCLUSIVE, experiment="em200")])

        assert [v.verdict for v, _ in book.written] == [Verdict.REJECTED, Verdict.INCONCLUSIVE]
        assert len({i for _, i in book.written}) == 2  # each has its own id

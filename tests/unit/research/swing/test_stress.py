"""EM-235: the survivorship stresses of a frozen candidate, and the candidate's own seal."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from tests.unit.research.swing.support import dataset, series, sessions

from emporos.core.errors import ConfigurationError
from emporos.research.index_membership import AsOfMembership, IndexChange, MembershipTimeline
from emporos.research.swing.candidate import FrozenCandidate
from emporos.research.swing.stress import (
    AsOf,
    LateJoinersOut,
    LateListedOut,
    late_joiners,
    late_listed,
)

DAYS = sessions(30, date(2018, 1, 1))
LATE_START = DAYS[10]
CUTOFF = DAYS[2]
A, B, C = "NSE:1", "NSE:2", "NSE:3"


def world():  # type: ignore[no-untyped-def]
    flat = [("100", "100")] * 30
    return dataset(
        series(A, DAYS, flat),
        series(B, DAYS, flat),
        series(C, DAYS[10:], flat[10:]),  # C lists on the tenth session
    )


class TestLateListed:
    def test_a_name_whose_first_session_is_after_the_cutoff_is_late(self) -> None:
        assert late_listed(world(), CUTOFF) == {C: DAYS[10]}

    def test_the_stress_drops_it_and_says_why(self) -> None:
        stressed = LateListedOut(CUTOFF).apply(world())

        assert stressed.dataset.instrument_ids == (A, B)
        assert stressed.membership is None
        assert "first daily session" in stressed.removed[C]


class TestLateJoiners:
    def membership(self) -> AsOfMembership:
        n100 = MembershipTimeline(
            "NIFTY 100", {"AA"},
            [IndexChange("NIFTY 100", DAYS[15], frozenset({"BB", "AA"}), frozenset(), "s")],
        )  # fmt: skip
        mid = MembershipTimeline(
            "NIFTY MIDCAP 150", {"BB"},
            [IndexChange("NIFTY MIDCAP 150", DAYS[15], frozenset(), frozenset({"BB"}), "s")],
        )  # fmt: skip
        return AsOfMembership([n100, mid], {"AA": A, "BB": B})

    def test_a_name_that_entered_the_union_in_the_window_is_a_joiner(self) -> None:
        joined = late_joiners(self.membership(), {"AA": A, "BB": B}, CUTOFF, DAYS[-1], [DAYS[15]])

        assert joined == {A: DAYS[15]}  # BB only moved from the midcap index to NIFTY 100

    def test_changes_outside_the_window_are_not_looked_at(self) -> None:
        assert late_joiners(self.membership(), {"AA": A}, DAYS[20], DAYS[-1], [DAYS[15]]) == {}

    def test_the_stress_drops_the_late_listed_and_the_joiners(self) -> None:
        stressed = LateJoinersOut(CUTOFF, {A: DAYS[15]}).apply(world())

        assert stressed.dataset.instrument_ids == (B,)
        assert set(stressed.removed) == {A, C}
        assert "entered" in stressed.removed[A]


class TestAsOf:
    def test_every_name_stays_and_the_membership_travels_with_the_universe(self) -> None:
        membership = AsOfMembership([MembershipTimeline("NIFTY 100", {"AA"}, [])], {"AA": A})

        stressed = AsOf(membership).apply(world())

        assert stressed.dataset.instrument_ids == (A, B, C)
        assert stressed.membership is membership
        assert stressed.removed == {}


class TestFrozenCandidate:
    def write(self, tmp_path: Path, content: bytes = b"declared") -> Path:
        pinned = tmp_path / "declaration.yaml"
        pinned.write_bytes(content)
        digest = hashlib.sha256(b"declared").hexdigest()
        path = tmp_path / "candidate.yaml"
        path.write_text(
            f"candidate: c\ncell: k\narm: {{split: '60/40'}}\nscreen_id: SWG-1\ncode_commit: abc\n"
            f"declaration: {{file: {pinned}, sha256: {digest}}}\n"
            f"sleeves:\n- nested: {{file: {pinned}, sha256: {digest}}}\n",
            encoding="utf-8",
        )
        return path

    def test_it_reads_the_arm_and_every_pinned_file_wherever_it_sits(self, tmp_path: Path) -> None:
        candidate = FrozenCandidate.load(self.write(tmp_path))

        assert (candidate.name, candidate.cell, dict(candidate.arm)) == (
            "c",
            "k",
            {"split": "60/40"},
        )
        assert len(candidate.pinned) == 1  # the same file twice is one pin

    def test_it_verifies_while_nothing_has_moved(self, tmp_path: Path) -> None:
        FrozenCandidate.load(self.write(tmp_path)).verify()

    def test_it_refuses_when_a_pinned_file_changed(self, tmp_path: Path) -> None:
        candidate = FrozenCandidate.load(self.write(tmp_path))
        (tmp_path / "declaration.yaml").write_bytes(b"edited after the freeze")

        with pytest.raises(ConfigurationError, match="frozen and these inputs changed"):
            candidate.verify()

    def test_it_refuses_when_a_pinned_file_is_gone(self, tmp_path: Path) -> None:
        candidate = FrozenCandidate.load(self.write(tmp_path))
        (tmp_path / "declaration.yaml").unlink()

        with pytest.raises(ConfigurationError, match="declaration.yaml"):
            candidate.verify()

    def test_a_file_that_is_not_a_candidate_is_a_configuration_error(self, tmp_path: Path) -> None:
        path = tmp_path / "x.yaml"
        path.write_text("candidate: c\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="not a frozen candidate"):
            FrozenCandidate.load(path)

    def test_the_committed_candidate_still_verifies(self) -> None:
        FrozenCandidate.load(Path("docs/research/profit/candidates/a4-60-40.yaml")).verify()

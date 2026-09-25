"""EM-239: the posture and context wiring, and the exit codes of the cue collection."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.cli import posture_commands
from emporos.cli.experiment_commands import research_app
from emporos.core.clock import IST
from emporos.eventtrader.events import MarketEvent
from emporos.research.filings.polite import SourceRefused
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.market_context.global_cues import FEEDS, YAHOO
from tests.unit.research.market_context.test_context import NIFTY, VIX, Loader, series
from tests.unit.research.market_context.test_open_interest import DAYS, future
from tests.unit.research.market_context.test_posture_state import (
    SESSIONS,
    TARGET,
    FakeStore,
    flat_series,
    nifty_closes,
    vix_closes,
)

RUNNER = CliRunner()


def names(count: int) -> dict[str, str]:
    return {f"S{n}": f"NSE:{n}" for n in range(count)}


def test_the_posture_inputs_are_built_over_a_loader_and_the_close_table_is_cached(
    tmp_path: Path,
) -> None:
    data = {NIFTY: flat_series(NIFTY, nifty_closes()), VIX: flat_series(VIX, vix_closes())}
    for instrument in names(35).values():
        data[instrument] = flat_series(instrument, [50.0 + n for n in range(len(SESSIONS))])
    closes = tmp_path / "closes.json"
    inputs = posture_commands.build_posture_inputs(
        Loader(data), FakeStore([]), tmp_path / "cues", YAHOO, names(35),
        SESSIONS[0], SESSIONS[-1], closes,
    )  # fmt: skip
    state = inputs.for_day(TARGET).state
    assert state["nifty_prev_close"] == 164.0
    assert state["breadth_up_share"] == 1.0 and state["breadth_names"] == 35.0
    assert state["material_filings_prev_day"] == 0.0
    assert "sp500_prev_close_pct" not in state  # no cue files were collected
    assert closes.exists()
    again = posture_commands.build_posture_inputs(
        Loader({NIFTY: data[NIFTY], VIX: data[VIX]}),  # the 35 names are not asked again
        FakeStore([]), tmp_path / "cues", YAHOO, names(35), SESSIONS[0], SESSIONS[-1], closes,
    )  # fmt: skip
    assert again.for_day(TARGET).state["breadth_names"] == 35.0


def test_the_context_builder_carries_the_open_interest(tmp_path: Path) -> None:
    files = FoDayStore(tmp_path / "fo")
    for day in DAYS[:3]:
        files.write(day, [future(day, DAYS[-1], 1000, "100")])
    data = {
        "NSE:1": series("NSE:1", 100, 1),
        NIFTY: series(NIFTY, 200, 2),
        VIX: series(VIX, 15, 0.1),
    }
    context = posture_commands.build_context_builder(
        Loader(data), SESSIONS[0], SESSIONS[-1], tmp_path / "fo"
    )
    at = datetime.combine(DAYS[3], time(11, 0), tzinfo=IST)
    event = MarketEvent("NSE:1", "NSE:1", "ABB", at, at, "filing", "Updates", "s", "t")
    assert context.context(event, at).lines["fut_open_interest"] == 1000.0


class TestCollectGlobalCues:
    def run(
        self, monkeypatch: pytest.MonkeyPatch, outcome: list[str] | Exception, *extra: str
    ) -> int:
        async def fake(*_args: object) -> list[str]:
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(posture_commands, "_collect", fake)
        return RUNNER.invoke(research_app, ["collect-global-cues", *extra]).exit_code

    def test_success_refusal_and_failures_have_their_own_exit_codes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self.run(monkeypatch, []) == 0
        assert self.run(monkeypatch, SourceRefused("429")) == 2
        assert self.run(monkeypatch, ["^GSPC: timeout"]) == 2

    def test_an_unknown_source_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self.run(monkeypatch, [], "--source", "bloomberg") == 1
        assert set(FEEDS) == {"fred", "yahoo"}

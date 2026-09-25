"""EM-221: the corporate-actions source asks politely, once per name, and stops when refused."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.cli.corporate_actions_commands import ActionCollection
from emporos.core.clock import FixedClock
from emporos.research.corporate_actions import CorporateActionLedger
from emporos.research.nse_corporate_actions import ActionsRefused, NseCorporateActionSource

FIRST, LAST = date(2016, 10, 3), date(2026, 3, 18)
GOOD = [
    {"symbol": "X", "exDate": "07-Sep-2017", "subject": "Bonus 1:1", "isin": "I", "series": "EQ"}
]


class RecordingSleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSource:
    async def test_the_request_names_the_window_and_an_honest_client(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=GOOD)

        source = NseCorporateActionSource(client(handler), RecordingSleeper())
        (action,) = await source.actions("M&M", FIRST, LAST)

        request = seen[0]
        assert request.url.params["symbol"] == "M&M"
        assert request.url.params["from_date"] == "03-10-2016"
        assert request.url.params["to_date"] == "18-03-2026"
        assert "emporos-research" in request.headers["user-agent"]
        assert action.subject == "Bonus 1:1"
        assert source.url_for("M&M", FIRST, LAST).startswith("https://www.nseindia.com/api/")

    async def test_requests_are_spaced_but_the_first_is_not_delayed(self) -> None:
        sleeper = RecordingSleeper()
        source = NseCorporateActionSource(
            client(lambda r: httpx.Response(200, json=[])), sleeper, seconds_between_requests=3.0
        )
        for symbol in ("A", "B", "C"):
            await source.actions(symbol, FIRST, LAST)

        assert sleeper.slept == [3.0, 3.0]

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_a_refusal_stops_the_run(self, status: int) -> None:
        source = NseCorporateActionSource(
            client(lambda r: httpx.Response(status)), RecordingSleeper()
        )

        with pytest.raises(ActionsRefused, match=str(status)):
            await source.actions("X", FIRST, LAST)

    def test_it_will_not_hammer(self) -> None:
        with pytest.raises(ValueError, match="a second apart"):
            NseCorporateActionSource(client(lambda r: httpx.Response(200)), RecordingSleeper(), 0.5)


class TestCollection:
    def ledger(self, tmp_path: Path) -> CorporateActionLedger:
        return CorporateActionLedger(tmp_path / "a.jsonl", tmp_path / "c.jsonl")

    async def test_a_collected_name_is_not_asked_again(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["symbol"])
            return httpx.Response(200, json=GOOD)

        ledger = self.ledger(tmp_path)
        source = NseCorporateActionSource(client(handler), RecordingSleeper())
        collection = ActionCollection(source, ledger, FixedClock(datetime(2026, 9, 25, tzinfo=UTC)))

        await collection.run(["A", "B"], FIRST, LAST)
        added, failed = await collection.run(["A", "B", "C"], FIRST, LAST)

        assert calls == ["A", "B", "C"]
        assert (added, failed) == (1, [])
        assert ledger.collected_symbols() == frozenset({"A", "B", "C"})

    async def test_a_refusal_propagates_and_leaves_earlier_names_recorded(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["symbol"] == "B":
                return httpx.Response(403)
            return httpx.Response(200, json=GOOD)

        ledger = self.ledger(tmp_path)
        source = NseCorporateActionSource(client(handler), RecordingSleeper())
        collection = ActionCollection(source, ledger, FixedClock(datetime(2026, 9, 25, tzinfo=UTC)))

        with pytest.raises(ActionsRefused):
            await collection.run(["A", "B", "C"], FIRST, LAST)

        assert ledger.collected_symbols() == frozenset({"A"})

    async def test_a_failure_on_one_name_is_reported_and_the_run_goes_on(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["symbol"] == "A":
                return httpx.Response(500)
            return httpx.Response(200, json=GOOD)

        ledger = self.ledger(tmp_path)
        source = NseCorporateActionSource(client(handler), RecordingSleeper())
        collection = ActionCollection(source, ledger, FixedClock(datetime(2026, 9, 25, tzinfo=UTC)))

        added, failed = await collection.run(["A", "B"], FIRST, LAST)

        assert (added, failed) == (1, ["A"])
        assert ledger.collected_symbols() == frozenset({"B"})

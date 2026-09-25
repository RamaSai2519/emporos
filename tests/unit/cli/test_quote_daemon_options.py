"""EM-246: the quotes-only process with the option quotes switched on. Across the whole day it still
reaches Angel One at login, quotes and logout only, the stock quotes are all there, and the option
files upload under their own prefix."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from emporos.broker.angelone.endpoints import Endpoints
from emporos.cli.quote_daemon import QuoteDaemon
from emporos.cli.quote_recording import OptionRecordingPlan, QuoteRecordingPlan
from emporos.core.clock import IST, FixedClock
from emporos.quotes.contract import ContractBook
from emporos.quotes.recorder import RecorderSettings
from emporos.quotes.sink import read_day
from tests.support.fakes import AdvancingSleeper, FixedJitter
from tests.unit.cli.test_quote_daemon import ALLOWED, AngelOne, Factory, Files, settings

IDS = ("NSE:1", "NSE:2")
UNDERLYINGS = 'underlyings:\n  - {symbol: TCS, spot_id: "NSE:11536"}\n'


class AngelWithOptions(AngelOne):
    """Any exchange in the quote call answers; an option's tradingSymbol is its contract."""

    def _entries(self, tokens: list[str]) -> list[dict[str, object]]:
        entries = super()._entries(tokens)
        return [{**e, "opnInterest": 1234} for e in entries]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == Endpoints.QUOTE.path:
            self.paths.append(request.url.path)
            body = json.loads(request.content)
            fetched: list[dict[str, object]] = []
            for exchange, tokens in body["exchangeTokens"].items():
                fetched += [{**e, "exchange": exchange} for e in self._entries(tokens)]
            return httpx.Response(
                200,
                json={"status": True, "message": "SUCCESS", "errorcode": "",
                      "data": {"fetched": fetched, "unfetched": []}},
            )  # fmt: skip
        return super().__call__(request)


class Master:
    async def load(self) -> ContractBook:
        rows = []
        for strike in (24000, 24100, 24200):
            for right in ("CE", "PE"):
                rows.append(
                    {
                        "token": f"{strike}{right == 'CE'}",
                        "symbol": f"NIFTY29SEP26{strike}{right}",
                        "name": "NIFTY", "expiry": "29SEP2026", "strike": f"{strike * 100}.0",
                        "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
                    }
                )  # fmt: skip
        return ContractBook.from_master_rows(rows)


async def test_a_day_with_options_still_touches_only_login_quotes_and_logout(
    tmp_path: Path,
) -> None:
    clock = FixedClock(datetime(2026, 9, 25, 8, 41, tzinfo=IST))
    angel, files = AngelWithOptions(clock), Files()
    underlyings = tmp_path / "underlyings.yaml"
    underlyings.write_text(UNDERLYINGS)
    plan = QuoteRecordingPlan(
        IDS, tmp_path / "quotes", RecorderSettings(),
        OptionRecordingPlan(tmp_path / "options", underlyings),
    )  # fmt: skip
    run = QuoteDaemon(
        settings(), plan, clock, AdvancingSleeper(clock), FixedJitter(), Factory(angel), files,
        Master(),
    )  # fmt: skip

    result = await run.run()

    assert result.exit_code == 0
    assert set(angel.paths) == ALLOWED
    assert read_day(tmp_path / "quotes", datetime(2026, 9, 25).date()).num_rows == 375 * len(IDS)
    assert any(k.startswith("quotes-options/date=2026-09-25/part-") for k in files.keys)
    assert any(k.startswith("quotes/date=2026-09-25/part-") for k in files.keys)


async def test_a_missing_underlyings_list_falls_back_to_the_indexes_and_keeps_the_stock_quotes(
    tmp_path: Path,
) -> None:
    clock = FixedClock(datetime(2026, 9, 25, 8, 41, tzinfo=IST))
    angel, files = AngelWithOptions(clock), Files()
    plan = QuoteRecordingPlan(
        IDS, tmp_path / "quotes", RecorderSettings(),
        OptionRecordingPlan(tmp_path / "options", tmp_path / "absent.yaml"),
    )  # fmt: skip
    run = QuoteDaemon(
        settings(), plan, clock, AdvancingSleeper(clock), FixedJitter(), Factory(angel), files,
        Master(),
    )  # fmt: skip

    result = await run.run()

    assert result.exit_code == 0
    assert read_day(tmp_path / "quotes", datetime(2026, 9, 25).date()).num_rows == 375 * len(IDS)

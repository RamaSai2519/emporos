"""EM-236: the quotes-only process, end to end over a scripted Angel One.

The proof the head asked for: across a whole day the process reaches Angel One at exactly three
endpoints (login, quotes, logout). It never touches an order endpoint, the order book, positions or
funds, and no strategy or risk code is involved in building it."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pyotp
import pytest
from typer.testing import CliRunner

from emporos.broker.angelone.endpoints import Endpoints
from emporos.cli.quote_daemon import QuoteDaemon
from emporos.cli.quote_recording import QuoteRecordingPlan
from emporos.core.clock import IST, FixedClock
from emporos.core.config import Settings
from emporos.quotes.day import DayOutcome
from emporos.quotes.recorder import RecorderSettings
from emporos.quotes.sink import read_day
from tests.support.fakes import AdvancingSleeper, FixedJitter, token_payload

IDS = ("NSE:1", "NSE:2", "NSE:3")
SECRET = pyotp.random_base32()
ALLOWED = {Endpoints.LOGIN.path, Endpoints.QUOTE.path, Endpoints.LOGOUT.path}


def settings() -> Settings:
    return Settings(
        _env_file=None,
        ANGELONE_API_KEY="k", ANGELONE_CLIENT_CODE="C1", ANGELONE_PASSWORD="1",
        ANGELONE_TOTP_SECRET=SECRET,
    )  # type: ignore[arg-type]  # fmt: skip


class AngelOne:
    """Answers by path; keeps every path it was asked for."""

    def __init__(self, clock: FixedClock, stale: bool = False) -> None:
        self.paths: list[str] = []
        self.clock, self.stale = clock, stale

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == Endpoints.LOGIN.path:
            data: object = token_payload("a")
        elif request.url.path == Endpoints.QUOTE.path:
            body = json.loads(request.content)
            data = {"fetched": self._entries(body["exchangeTokens"]["NSE"]), "unfetched": []}
        else:
            data = None
        return httpx.Response(200, json={"status": True, "message": "SUCCESS", "errorcode": "",
                                         "data": data})  # fmt: skip

    def _entries(self, tokens: list[str]) -> list[dict[str, object]]:
        day = self.clock.now().astimezone(IST)
        stamp = day.replace(day=day.day - 1 if self.stale else day.day)
        return [
            {
                "exchange": "NSE", "tradingSymbol": f"S{t}-EQ", "symbolToken": t, "ltp": 100.5,
                "open": 100, "high": 101, "low": 99, "close": 100, "tradeVolume": 7,
                "lowerCircuit": 90, "upperCircuit": 110,
                "exchTradeTime": stamp.strftime("%d-%b-%Y %H:%M:%S"),
                "depth": {"buy": [{"price": 100.45, "quantity": 30, "orders": 2}],
                          "sell": [{"price": 100.55, "quantity": 40, "orders": 3}]},
            }
            for t in tokens
        ]  # fmt: skip


class Factory:
    def __init__(self, handler: AngelOne) -> None:
        self._handler = handler

    def create(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://apiconnect.angelone.in", transport=httpx.MockTransport(self._handler)
        )


class Files:
    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.keys[key] = data

    async def stat(self, key: str) -> None:
        return None


def daemon(
    tmp_path: Path, at: datetime, stale: bool = False
) -> tuple[QuoteDaemon, AngelOne, Files]:
    clock = FixedClock(at)
    angel, files = AngelOne(clock, stale), Files()
    plan = QuoteRecordingPlan(IDS, tmp_path, RecorderSettings())
    return (
        QuoteDaemon(
            settings(), plan, clock, AdvancingSleeper(clock), FixedJitter(), Factory(angel), files
        ),
        angel,
        files,
    )


async def test_a_day_touches_only_login_quotes_and_logout_and_uploads_what_it_wrote(
    tmp_path: Path,
) -> None:
    run, angel, files = daemon(tmp_path, datetime(2026, 9, 25, 8, 41, tzinfo=IST))

    result = await run.run()

    assert result.outcome is DayOutcome.RECORDED and result.exit_code == 0
    assert set(angel.paths) == ALLOWED  # no order, order book, position or funds call
    assert angel.paths[0] == Endpoints.LOGIN.path and angel.paths[-1] == Endpoints.LOGOUT.path
    table = read_day(tmp_path, datetime(2026, 9, 25).date())
    assert table.num_rows == 375 * len(IDS)
    assert result.uploaded == len(files.keys) >= 1
    assert all(k.startswith("quotes/date=2026-09-25/part-") for k in files.keys)


async def test_a_holiday_writes_and_uploads_nothing_and_still_logs_out(tmp_path: Path) -> None:
    run, angel, files = daemon(tmp_path, datetime(2026, 9, 25, 8, 41, tzinfo=IST), stale=True)

    result = await run.run()

    assert result.outcome is DayOutcome.HOLIDAY and result.exit_code == 0
    assert list(tmp_path.iterdir()) == [] and files.keys == {}
    assert angel.paths[-1] == Endpoints.LOGOUT.path


async def test_a_weekend_start_never_logs_in_at_all(tmp_path: Path) -> None:
    run, angel, _ = daemon(tmp_path, datetime(2026, 9, 26, 8, 41, tzinfo=IST))

    result = await run.run()

    assert result.outcome is DayOutcome.NOT_A_WEEKDAY
    assert set(angel.paths) <= {Endpoints.LOGOUT.path}  # no login, no quote


async def test_a_failed_upload_is_the_exit_code_so_the_unit_retries(tmp_path: Path) -> None:
    run, _, files = daemon(tmp_path, datetime(2026, 9, 25, 15, 0, tzinfo=IST))

    async def refuse(key: str, data: bytes) -> None:
        raise OSError("denied")

    files.put = refuse  # type: ignore[method-assign]
    result = await run.run()

    assert result.upload_failed >= 1 and result.exit_code == 2


def test_the_command_exists_and_takes_no_strategy_or_account_options() -> None:
    from emporos.cli.main import app

    out = CliRunner().invoke(app, ["worker", "record-quotes", "--help"]).output

    assert "--quotes-dir" in out and "--quote-interval" in out
    for forbidden in ("--strategies", "--start", "--account", "--cash", "--acknowledge"):
        assert forbidden not in out


@pytest.mark.parametrize(
    "module", ["emporos.cli.quote_daemon", "emporos.broker.angelone.quote_source"]
)
def test_the_modules_do_not_reach_execution_risk_strategies_or_mongo(module: str) -> None:
    import importlib
    import sys

    importlib.import_module(module)
    source = Path(sys.modules[module].__file__ or "").read_text()
    for banned in ("emporos.execution", "emporos.risk", "emporos.strategies", "pymongo",
                   "place_order", "AngelOneBroker"):  # fmt: skip
        assert banned not in source

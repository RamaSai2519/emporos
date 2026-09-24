"""Record live SmartAPI exchanges as scrubbed contract-test fixtures (EM-47).

    pipenv run python scripts/record_angelone_fixtures.py

Uses the real `.env` credentials and the *read-only* endpoints only — it never places, modifies
or cancels an order (orders are IP-gated to the production host's registered static IP, not to
this machine).
It performs a real login, which supersedes any other session for this client code.

Safety, in layers:
  1. `FixtureScrubber` replaces every secret / personal / balance value by key policy.
  2. `LeakGuard` then refuses to write a fixture if any known secret, or anything shaped like a
     JWT, is still present anywhere in it.
  3. The script prints fixture names and HTTP statuses only — never a payload.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx

from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.session import PyotpTotp
from emporos.broker.angelone.transport import (
    AngelOneHttpClientFactory,
    HttpRestTransport,
    RestRequest,
)
from emporos.broker.errors import BrokerError, BrokerRateLimitedError
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "angelone"
PAUSE_SECONDS = 1.2  # comfortably above the 1 request/second endpoint limits
SBIN_NSE_TOKEN = "3045"
_JWT_SHAPE = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")


@dataclass(frozen=True)
class Exchange:
    """One raw HTTP round trip, as it went over the wire."""

    name: str
    method: str
    path: str
    request_body: Any
    header_names: tuple[str, ...]
    status: int
    response_text: str


class ExchangeCapture:
    """An httpx response hook that keeps every raw exchange, however it is later classified."""

    def __init__(self) -> None:
        self._pending_name = ""
        self.exchanges: list[Exchange] = []

    def next_is(self, name: str) -> None:
        self._pending_name = name

    def skip_next(self) -> None:
        """The next exchange is scaffolding (e.g. the login that arms a burst); do not keep it."""
        self._pending_name = ""

    async def __call__(self, response: httpx.Response) -> None:
        await response.aread()
        if not self._pending_name:
            return
        request = response.request
        self.exchanges.append(
            Exchange(
                name=self._pending_name,
                method=request.method,
                path=request.url.path,
                request_body=json.loads(request.content) if request.content else None,
                header_names=tuple(
                    sorted(h for h in request.headers if h.startswith("x-") or h == "authorization")
                ),
                status=response.status_code,
                response_text=response.text,
            )
        )


class FixtureScrubber:
    """Replaces secrets, personal data and balances with fixed fakes, keeping the exact shape."""

    _REQUEST: ClassVar[Mapping[str, str]] = {
        "clientcode": "A0000000",
        "password": "0000",
        "totp": "000000",
        "refreshToken": "scrubbed-refresh-token",
    }
    _RESPONSE: ClassVar[Mapping[str, str]] = {
        "jwtToken": "scrubbed-jwt-token",
        "refreshToken": "scrubbed-refresh-token",
        "feedToken": "scrubbed-feed-token",
        "clientcode": "A0000000",
        "name": "Test User",
        "email": "test@example.invalid",
        "mobileno": "0000000000",
        "lastlogintime": "2026-01-01 00:00:00",
    }
    _FUNDS_KEYS = frozenset(
        {
            "net", "availablecash", "availableintradaypayin", "availablelimitmargin", "collateral",
            "m2munrealized", "m2mrealized", "utiliseddebits", "utilisedpayout",
        }
    )  # fmt: skip

    def scrub(self, exchange: Exchange) -> dict[str, Any]:
        try:
            response: Any = json.loads(exchange.response_text)
        except ValueError:
            response = None
        is_funds = exchange.path.endswith("/getRMS")
        return {
            "name": exchange.name,
            "provenance": "recorded",
            "recorded_on": datetime.now(IST).date().isoformat(),
            "request": {
                "method": exchange.method,
                "path": exchange.path,
                "body": self._walk(exchange.request_body, self._REQUEST, False),
                "header_names": list(exchange.header_names),
            },
            "response": {
                "status": exchange.status,
                "body": None
                if response is None
                else self._walk(response, self._RESPONSE, is_funds),
                "body_text": exchange.response_text if response is None else None,
            },
        }

    def _walk(self, value: Any, policy: Mapping[str, str], funds: bool) -> Any:
        if isinstance(value, dict):
            return {k: self._replace(k, v, policy, funds) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(item, policy, funds) for item in value]
        return value

    def _replace(self, key: str, value: Any, policy: Mapping[str, str], funds: bool) -> Any:
        if key in policy and value is not None:
            return policy[key]
        if funds and key in self._FUNDS_KEYS and value is not None:
            return "100000.00" if key in {"net", "availablecash"} else "0.00"
        return self._walk(value, policy, funds)


class LeakGuard:
    """Last line of defence: no known secret, and nothing JWT-shaped, may reach a fixture file."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = [s for s in secrets if s]

    def add(self, *secrets: str | None) -> None:
        self._secrets.extend(s for s in secrets if s)

    def assert_clean(self, fixture: Mapping[str, Any]) -> None:
        text = json.dumps(fixture)
        if _JWT_SHAPE.search(text):
            raise RuntimeError(f"{fixture['name']}: something JWT-shaped survived scrubbing")
        for secret in self._secrets:
            # a short secret (a 4-digit PIN) only leaks as a whole JSON string, not inside a number
            leaked = secret in text if len(secret) >= 8 else f'"{secret}"' in text
            if leaked:
                raise RuntimeError(f"{fixture['name']}: a known secret survived scrubbing")


class FixtureWriter:
    def __init__(self, directory: Path, scrubber: FixtureScrubber, guard: LeakGuard) -> None:
        self._directory = directory
        self._scrubber = scrubber
        self._guard = guard

    def write(self, exchanges: Iterable[Exchange]) -> list[str]:
        self._directory.mkdir(parents=True, exist_ok=True)
        written = []
        for exchange in exchanges:
            fixture = self._scrubber.scrub(exchange)
            self._guard.assert_clean(fixture)
            (self._directory / f"{exchange.name}.json").write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            written.append(f"{exchange.name} (HTTP {exchange.status})")
        return written


class LiveScenario:
    """The sequence of read-only calls to record. Feeds each new token to the leak guard."""

    def __init__(
        self, transport: HttpRestTransport, capture: ExchangeCapture, settings: Settings
    ) -> None:
        self._transport = transport
        self._capture = capture
        self._settings = settings

    async def run(self, guard: LeakGuard) -> None:
        settings = self._settings
        assert settings.angelone_client_code and settings.angelone_password
        assert settings.angelone_totp_secret
        client_code = settings.angelone_client_code
        login_body = {
            "clientcode": client_code,
            "password": settings.angelone_password,
            "totp": PyotpTotp(settings.angelone_totp_secret).code(),
        }
        tokens = await self._call("login_success", RestRequest(Endpoints.LOGIN, body=login_body))
        guard.add(*tokens.values())
        jwt = tokens["jwtToken"]

        await self._call("profile", RestRequest(Endpoints.PROFILE, bearer=jwt))
        await self._call("funds", RestRequest(Endpoints.FUNDS, bearer=jwt))
        # read-only account books (no orders are placed): recorded as the EMPTY-account shapes
        await self._call("order_book_empty", RestRequest(Endpoints.ORDER_BOOK, bearer=jwt))
        await self._call("trade_book_empty", RestRequest(Endpoints.TRADE_BOOK, bearer=jwt))
        await self._call("positions_empty", RestRequest(Endpoints.POSITIONS, bearer=jwt))
        await self._call("holdings_empty", RestRequest(Endpoints.HOLDINGS, bearer=jwt))
        await self._call("ltp", RestRequest(Endpoints.LTP, bearer=jwt, body=self._ltp_body()))
        await self._call(
            "quote_full", RestRequest(Endpoints.QUOTE, bearer=jwt, body=self._quote_body())
        )
        await self._record_candles(jwt)

        # error shapes, recorded from the live API
        bad_interval = {**self._candle_body("2026-01-05"), "interval": "BOGUS"}
        await self._refused(
            "candles_bad_interval", RestRequest(Endpoints.CANDLES, bearer=jwt, body=bad_interval)
        )
        await self._refused(
            "profile_invalid_token", RestRequest(Endpoints.PROFILE, bearer="not.a.token")
        )
        no_token = replace(Endpoints.PROFILE, authenticated=False)  # sends no Authorization at all
        await self._refused("profile_missing_token", RestRequest(no_token))
        wrong_totp = {**login_body, "totp": "000000"}
        await self._refused("login_bad_totp", RestRequest(Endpoints.LOGIN, body=wrong_totp))

        refresh = {"refreshToken": tokens["refreshToken"]}
        renewed = await self._call(
            "generate_tokens", RestRequest(Endpoints.GENERATE_TOKENS, bearer=jwt, body=refresh)
        )
        guard.add(*renewed.values())
        logout = RestRequest(
            Endpoints.LOGOUT, bearer=renewed["jwtToken"], body={"clientcode": client_code}
        )
        await self._call("logout", logout)
        await self._record_rate_limit(login_body, guard, client_code)

    async def _record_rate_limit(
        self, login_body: dict[str, str], guard: LeakGuard, client_code: str
    ) -> None:
        """Two logins with no pause between them: the second breaches the 1/s limit, and the API
        answers HTTP 403 with a plain-text body (recorded, not assumed)."""
        await asyncio.sleep(PAUSE_SECONDS)
        self._capture.skip_next()
        tokens = await self._transport.send(RestRequest(Endpoints.LOGIN, body=login_body))
        guard.add(*tokens.values())
        self._capture.next_is("login_rate_limited")
        try:
            await self._transport.send(RestRequest(Endpoints.LOGIN, body=login_body))
        except BrokerRateLimitedError:
            pass
        else:
            raise RuntimeError(
                "login_rate_limited: the API did not rate-limit a rapid second login"
            )
        await asyncio.sleep(PAUSE_SECONDS)
        self._capture.skip_next()
        logout = RestRequest(
            Endpoints.LOGOUT, bearer=tokens["jwtToken"], body={"clientcode": client_code}
        )
        await self._transport.send(logout)

    async def _record_candles(self, jwt: str) -> None:
        day = datetime.now(IST).date()
        for _ in range(7):  # walk back to the most recent session that has data
            day -= timedelta(days=1)
            if day.weekday() >= 5:
                continue
            request = RestRequest(Endpoints.CANDLES, bearer=jwt, body=self._candle_body(str(day)))
            if await self._call("candles_1m", request):
                return
        raise RuntimeError("no recent trading day returned candle data")

    @staticmethod
    def _ltp_body() -> dict[str, str]:
        return {"exchange": "NSE", "tradingsymbol": "SBIN-EQ", "symboltoken": SBIN_NSE_TOKEN}

    @staticmethod
    def _quote_body() -> dict[str, Any]:
        return {"mode": "FULL", "exchangeTokens": {"NSE": [SBIN_NSE_TOKEN]}}

    @staticmethod
    def _candle_body(day: str) -> dict[str, str]:
        return {
            "exchange": "NSE",
            "symboltoken": SBIN_NSE_TOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": f"{day} 09:15",
            "todate": f"{day} 09:19",
        }

    async def _refused(self, name: str, request: RestRequest) -> None:
        """Record a request the API is expected to refuse; not being refused is an error."""
        try:
            await self._call(name, request)
        except BrokerError:
            return
        raise RuntimeError(f"{name}: expected the API to refuse this request")

    async def _call(self, name: str, request: RestRequest) -> Any:
        await asyncio.sleep(PAUSE_SECONDS)
        self._capture.next_is(name)
        return await self._transport.send(request)


class FixtureRecorder:
    def __init__(self, settings: Settings, directory: Path = FIXTURE_DIR) -> None:
        self._settings = settings
        self._directory = directory

    async def record(self) -> list[str]:
        settings = self._settings
        secrets = [
            settings.angelone_api_key, settings.angelone_client_code,
            settings.angelone_password, settings.angelone_totp_secret,
        ]  # fmt: skip
        if not all(secrets):
            raise SystemExit("ANGELONE_* credentials are not all set")
        guard = LeakGuard(s for s in secrets if s)
        capture = ExchangeCapture()
        client = AngelOneHttpClientFactory().create()
        client.event_hooks["response"] = [capture]
        try:
            assert settings.angelone_api_key
            transport = HttpRestTransport(client, settings.angelone_api_key)
            await LiveScenario(transport, capture, settings).run(guard)
        finally:
            await client.aclose()
        return FixtureWriter(self._directory, FixtureScrubber(), guard).write(capture.exchanges)


async def main() -> None:
    print(f"recording on {SystemClock().now().astimezone(IST):%Y-%m-%d} (IST)")
    for line in await FixtureRecorder(Settings()).record():
        print("  recorded", line)


if __name__ == "__main__":
    asyncio.run(main())

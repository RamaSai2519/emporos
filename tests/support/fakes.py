"""Lightweight test doubles that implement the same Protocols as the production adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

import httpx

from emporos.broker.angelone.session import Session
from emporos.broker.angelone.transport import RestRequest
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.instruments.differ import InstrumentDiff
from emporos.instruments.downloader import DownloadedMaster
from emporos.persistence.migrations import IndexInfo
from emporos.persistence.object_store import ObjectInfo, ObjectNotFoundError
from emporos.persistence.schema import CollectionSpec, IndexSpec


class InMemorySchemaStore:
    """`SchemaStore` double: remembers collections and indexes in plain dicts."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, IndexInfo]] = {}

    async def collection_names(self) -> set[str]:
        return set(self._collections)

    async def create_collection(self, spec: CollectionSpec) -> None:
        self._collections.setdefault(spec.name, {})

    async def indexes(self, collection: str) -> Mapping[str, IndexInfo]:
        return dict(self._collections[collection])

    async def create_index(self, collection: str, index: IndexSpec) -> None:
        self._collections[collection][index.name] = IndexInfo(
            keys=index.keys,
            unique=index.unique,
            expire_after_seconds=index.expire_after_seconds,
        )

    def drop_index(self, collection: str, name: str) -> None:
        del self._collections[collection][name]

    def replace_index(self, collection: str, name: str, info: IndexInfo) -> None:
        self._collections[collection][name] = info


class InMemoryObjectStore:
    """`ObjectStore` double backed by a dict; etags change whenever the bytes do."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.get_calls = 0

    async def put(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    async def get(self, key: str) -> bytes:
        self.get_calls += 1
        if key not in self._objects:
            raise ObjectNotFoundError(key)
        return self._objects[key]

    async def stat(self, key: str) -> ObjectInfo | None:
        data = self._objects.get(key)
        return None if data is None else self._info(key, data)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return [
            self._info(key, data)
            for key, data in sorted(self._objects.items())
            if key.startswith(prefix)
        ]

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    @staticmethod
    def _info(key: str, data: bytes) -> ObjectInfo:
        return ObjectInfo(key, len(data), hashlib.md5(data, usedforsecurity=False).hexdigest())


class InMemoryCandleStore:
    """`HotCandleStore` double keyed by (instrument, timeframe, ts)."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, Timeframe, datetime], Candle] = {}

    async def upsert(self, candles: Sequence[Candle]) -> None:
        for candle in candles:
            self._bars[(candle.instrument_id, candle.timeframe, candle.ts)] = candle

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        return sorted(
            (
                bar
                for (inst, tf, ts), bar in self._bars.items()
                if inst == instrument_id and tf == timeframe and start <= ts < end
            ),
            key=lambda bar: bar.ts,
        )


class InMemoryInstrumentMasterStore:
    """`InstrumentMasterStore` double that applies diffs to a dict and counts writes."""

    def __init__(self, initial: Sequence[Instrument] = ()) -> None:
        self._current = {i.instrument_id: i for i in initial}
        self.apply_calls = 0

    async def load_current(self) -> list[Instrument]:
        return list(self._current.values())

    async def apply(self, diff: InstrumentDiff, at: datetime) -> None:
        self.apply_calls += 1
        for instrument in diff.removed:
            del self._current[instrument.instrument_id]
        for change in diff.changed:
            self._current[change.after.instrument_id] = change.after
        for instrument in diff.added:
            self._current[instrument.instrument_id] = instrument


class StubMasterSource:
    """`MasterSource` double returning a fixed master, or raising a fixed error."""

    def __init__(self, master: DownloadedMaster | None = None, error: Exception | None = None):
        self._master = master
        self._error = error

    async def download(self) -> DownloadedMaster:
        if self._error is not None:
            raise self._error
        assert self._master is not None
        return self._master


class RecordingAlertSink:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def raise_alert(self, name: str, message: str) -> None:
        self.alerts.append((name, message))


class ScriptedHttpServer:
    """An `httpx` transport double that replays queued replies (or raises queued errors).

    Records every request so tests can assert on paths, headers and bodies. Running out of
    script is a test bug, so it fails loudly instead of inventing a reply."""

    def __init__(self, base_url: str = "https://apiconnect.angelone.in") -> None:
        self._base_url = base_url
        self._script: deque[httpx.Response | Exception] = deque()
        self.requests: list[httpx.Request] = []

    def queue(self, *replies: httpx.Response | Exception) -> ScriptedHttpServer:
        self._script.extend(replies)
        return self

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(self))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"unscripted request: {request.method} {request.url.path}")
        reply = self._script.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok_reply(data: object = None) -> httpx.Response:
    """A SmartAPI success envelope. Floats in `data` are serialized as JSON numbers."""
    return httpx.Response(
        200,
        content=json.dumps({"status": True, "message": "SUCCESS", "errorcode": "", "data": data}),
    )


def failed_reply(message: str, code: str = "", http_status: int = 200) -> httpx.Response:
    """A SmartAPI `status: false` envelope (HTTP 200 unless told otherwise)."""
    body = {"status": False, "message": message, "errorcode": code, "data": None}
    return httpx.Response(http_status, content=json.dumps(body))


class AdvancingSleeper:
    """`Sleeper` double that advances a `FixedClock` instead of waiting: virtual time.

    Rounds up to whole microseconds (the clock's resolution) so a wait is never shortened."""

    def __init__(self, clock: FixedClock) -> None:
        self._clock = clock
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._clock.advance(timedelta(microseconds=math.ceil(seconds * 1_000_000)))
        await asyncio.sleep(0)  # let other tasks run, as a real sleep would


class FixedJitter:
    """`JitterSource` double: always returns the same fraction, or cycles through a fixed list."""

    def __init__(self, *fractions: float) -> None:
        self._fractions = fractions or (0.0,)
        self._calls = 0

    def fraction(self) -> float:
        value = self._fractions[self._calls % len(self._fractions)]
        self._calls += 1
        return value


class ScriptedRestTransport:
    """`RestTransport` double routed by endpoint name: each endpoint has its own reply queue.

    Replies are returned as the envelope `data`; exceptions are raised. Every request is kept."""

    def __init__(self, **script: Sequence[object]) -> None:
        self._script: dict[str, deque[object]] = {
            name: deque(items) for name, items in script.items()
        }
        self.requests: list[RestRequest] = []

    def queue(self, endpoint_name: str, *replies: object) -> ScriptedRestTransport:
        self._script.setdefault(endpoint_name, deque()).extend(replies)
        return self

    def sent_to(self, endpoint_name: str) -> list[RestRequest]:
        return [r for r in self.requests if r.endpoint.name == endpoint_name]

    async def send(self, request: RestRequest) -> object:
        self.requests.append(request)
        queue = self._script.get(request.endpoint.name)
        if not queue:
            raise AssertionError(f"unscripted call to {request.endpoint.name}")
        reply = queue.popleft()
        if isinstance(reply, BaseException):
            raise reply
        return reply


class FixedTotp:
    """`TotpSource` double."""

    def __init__(self, code: str = "123456") -> None:
        self._code = code

    def code(self) -> str:
        return self._code


class RecordingSessionStore:
    """`SessionStore` double that also counts saves and clears."""

    def __init__(self) -> None:
        self._session: Session | None = None
        self.saves = 0
        self.clears = 0

    def load(self) -> Session | None:
        return self._session

    def save(self, session: Session) -> None:
        self._session = session
        self.saves += 1

    def clear(self) -> None:
        self._session = None
        self.clears += 1


def token_payload(tag: str = "1") -> dict[str, object]:
    """A login/generateTokens `data` payload (fake tokens, real shape)."""
    return {
        "jwtToken": f"jwt-{tag}",
        "refreshToken": f"refresh-{tag}",
        "feedToken": f"feed-{tag}",
        "state": None,
    }


def make_instrument(
    token: str = "3045", exchange: Exchange = Exchange.NSE, symbol: str | None = None
) -> Instrument:
    """A valid cash instrument for tests."""
    return Instrument(
        exchange, token, symbol or f"SYM{token}-EQ", f"Name {token}", 1, Money.of("0.05")
    )


class RecordingSubscriptionTransport:
    """`SubscriptionTransport` double: records every call, and can be made to fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.fail_with: Exception | None = None

    async def subscribe(self, instruments: Sequence[Instrument]) -> None:
        self._record("subscribe", instruments)

    async def unsubscribe(self, instruments: Sequence[Instrument]) -> None:
        self._record("unsubscribe", instruments)

    def _record(self, kind: str, instruments: Sequence[Instrument]) -> None:
        self.calls.append((kind, tuple(i.instrument_id for i in instruments)))
        if self.fail_with is not None:
            raise self.fail_with

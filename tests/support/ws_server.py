"""An in-process SmartWebSocketV2 server double: real sockets, the real wire protocol.

It answers text `ping` with `pong`, records handshake headers and JSON control messages, and can
push binary frames, drop every connection, go silent (no pongs) or reject handshakes with a 401 —
the failure modes the market-feed client must survive."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response


class FakeFeedServer:
    def __init__(self) -> None:
        self.handshake_headers: list[dict[str, str]] = []
        self.control_messages: list[dict[str, Any]] = []
        self.pings = 0
        self.answer_pings = True
        self.reject_handshakes = 0  # reject this many upcoming handshakes with HTTP 401
        self._connections: set[ServerConnection] = set()
        self._server: Server | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @asynccontextmanager
    async def running(self, port: int = 0) -> AsyncIterator[FakeFeedServer]:
        await self.start(port)
        try:
            yield self
        finally:
            await self.stop()

    async def start(self, port: int = 0) -> None:
        self._server = await serve(
            self._handle,
            "127.0.0.1",
            port,
            process_request=self._process_request,
            ping_interval=None,
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop_all()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def send_frame(self, frame: bytes) -> None:
        await asyncio.gather(*(c.send(frame) for c in list(self._connections)))

    async def send_text(self, text: str) -> None:
        await asyncio.gather(*(c.send(text) for c in list(self._connections)))

    async def drop_all(self) -> None:
        await asyncio.gather(
            *(c.close(code=1011, reason="test drop") for c in list(self._connections)),
            return_exceptions=True,
        )

    async def wait_for(self, predicate: Any, timeout: float = 3.0) -> None:
        """Poll until `predicate()` holds; fail loudly rather than hang the suite."""
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not reached in time")
            await asyncio.sleep(0.005)

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        self.handshake_headers.append({k.lower(): v for k, v in request.headers.items()})
        if self.reject_handshakes > 0:
            self.reject_handshakes -= 1
            return connection.respond(HTTPStatus.UNAUTHORIZED, "rejected\n")
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        self._connections.add(connection)
        try:
            async for message in connection:
                if message == "ping":
                    self.pings += 1
                    if self.answer_pings:
                        await connection.send("pong")
                elif isinstance(message, str):
                    self.control_messages.append(json.loads(message))
        finally:
            self._connections.discard(connection)

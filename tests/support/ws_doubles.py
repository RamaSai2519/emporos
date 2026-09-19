"""Test doubles for the market-feed client's collaborators (auth, handler, listener, sleeper)."""

from __future__ import annotations

import asyncio

from emporos.broker.angelone.feed_auth import FeedAuth


class RecordingAuth:
    """`FeedAuthProvider` double: hands out FeedAuth and records whether a refresh was requested."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def feed_auth(self, *, refresh: bool) -> FeedAuth:
        self.calls.append(refresh)
        return FeedAuth("jwt-1", "key-1", "C1", "feed-2" if refresh else "feed-1")


class RecordingHandler:
    def __init__(self, fail_on: bytes | None = None) -> None:
        self.frames: list[bytes] = []
        self._fail_on = fail_on

    def on_frame(self, frame: bytes) -> None:
        if frame == self._fail_on:
            raise RuntimeError("handler bug")
        self.frames.append(frame)


class RecordingListener:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnects: list[str] = []

    async def on_connected(self) -> None:
        self.connected += 1

    async def on_disconnected(self, reason: str) -> None:
        self.disconnects.append(reason)


class RealSleeper:
    """Sleeps for real (the socket I/O is real) but records every reconnect delay."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        await asyncio.sleep(seconds)


class FixedJitter:
    def fraction(self) -> float:
        return 0.0

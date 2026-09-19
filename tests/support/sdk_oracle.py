"""The pinned `smartapi-python` SDK, used ONLY as a contract-test oracle (Decision 4).

Loading it hermetically matters: importing `SmartApi.smartConnect` runs
`requests.get("https://api.ipify.org")` in a class body, and instantiating `SmartConnect`
creates `logs/<date>/app.log` in the working directory. This module suppresses both, so the
contract suite needs no network and leaves nothing behind.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import requests


@dataclass(frozen=True)
class SdkRequest:
    """One request the SDK would have sent."""

    route: str
    method: str
    path: str
    params: Mapping[str, Any]


def _refuse_network(*_: object, **__: object) -> None:
    raise requests.ConnectionError("network is disabled while loading the SDK oracle")


def load_smart_connect() -> type:
    """Import the SDK's `SmartConnect` without letting it touch the network."""
    with mock.patch("requests.get", _refuse_network):
        from SmartApi.smartConnect import SmartConnect

    return SmartConnect


class SdkRequestRecorder:
    """Drives the real SDK methods and captures the requests they build.

    `replies` maps a route (e.g. "api.login") to the JSON body the "server" answers with, so the
    SDK's own response handling also runs over the recorded reality."""

    def __init__(self, replies: Mapping[str, Any], workdir: Path) -> None:
        self.requests: list[SdkRequest] = []
        smart_connect = load_smart_connect()
        recorder = self

        class Capturing(smart_connect):  # type: ignore[misc, valid-type]
            def _request(self, route: str, method: str, parameters: Any = None) -> Any:
                recorder.requests.append(
                    SdkRequest(route, method, self._routes[route], dict(parameters or {}))
                )
                return replies[route]

        previous = Path.cwd()
        os.chdir(workdir)  # the SDK writes ./logs/<date>/app.log on construction
        try:
            self.sdk: Any = Capturing(api_key="test-api-key")
        finally:
            os.chdir(previous)

    def only(self) -> SdkRequest:
        (request,) = self.requests
        return request

    def capture(self, call: Callable[[Any], object]) -> SdkRequest:
        """Run one SDK method and return the single request it made."""
        self.requests.clear()
        call(self.sdk)
        return self.only()

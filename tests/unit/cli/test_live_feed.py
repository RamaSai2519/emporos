"""The Angel One feed refuses to open without credentials, before it builds anything."""

from __future__ import annotations

import pytest

from emporos.broker.backoff import RandomJitter
from emporos.cli.live_feed import AngelOneFeedOpener, FeedRequest
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.session import SessionWindow
from emporos.session.bar_feed import ClosedBarQueue


async def test_no_credentials_means_no_feed() -> None:
    settings = Settings.model_construct(angelone_api_key=None, angelone_client_code=None)
    opener = AngelOneFeedOpener(settings, SystemClock(), AsyncioSleeper(), RandomJitter())
    request = FeedRequest(
        database=None,  # type: ignore[arg-type]
        instruments=InstrumentCache(),
        master=None,  # type: ignore[arg-type]
        bars=ClosedBarQueue(frozenset({Timeframe.M5})),
        window=SessionWindow(),
    )

    with pytest.raises(ConfigurationError, match="ANGELONE_API_KEY"):
        async with opener.open(request):
            pytest.fail("a feed was opened without credentials")  # pragma: no cover

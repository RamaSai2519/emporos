"""EM-185: the capture decorator remembers what risk's snapshot saw, once, without a re-fetch."""

from __future__ import annotations

from emporos.core.clock import FixedClock
from emporos.domain.money import Money
from emporos.risk.snapshot import InstrumentMarket
from emporos.session.decision_quotes import QuoteCapturingMarketFacts
from tests.support.risk import NOW


class Source:
    def __init__(self, market: InstrumentMarket) -> None:
        self.market = market
        self.calls = 0

    async def market_facts(self, instrument_id: str) -> InstrumentMarket:
        self.calls += 1
        return self.market


async def test_it_returns_the_market_unchanged_and_remembers_its_prices() -> None:
    market = InstrumentMarket(Money.of("100"), Money.of("99.9"), Money.of("100.1"), stale=False)
    source = Source(market)
    capture = QuoteCapturingMarketFacts(source, FixedClock(NOW), "broker_quote")

    assert await capture.market_facts("NSE:1") is market
    quote = capture.take("NSE:1")

    assert source.calls == 1  # one fetch, shared
    assert quote is not None
    assert (quote.ltp, quote.bid, quote.ask) == (
        Money.of("100"),
        Money.of("99.9"),
        Money.of("100.1"),
    )
    assert (quote.ts, quote.source) == (NOW, "broker_quote")


async def test_taking_consumes_the_quote() -> None:
    capture = QuoteCapturingMarketFacts(
        Source(InstrumentMarket(Money.of("100"), stale=False)), FixedClock(NOW), "q"
    )
    await capture.market_facts("NSE:1")

    assert capture.take("NSE:1") is not None
    assert capture.take("NSE:1") is None


async def test_a_market_with_no_prices_records_nothing() -> None:
    capture = QuoteCapturingMarketFacts(Source(InstrumentMarket(stale=True)), FixedClock(NOW), "q")
    await capture.market_facts("NSE:1")

    assert capture.take("NSE:1") is None

import pytest

from emporos.domain.instruments import Exchange, UnknownInstrumentError
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiff
from tests.support.instrument_rows import instrument


class StubStore:
    def __init__(self, instruments: list) -> None:  # type: ignore[type-arg]
        self._instruments = instruments

    async def load_current(self) -> list:  # type: ignore[type-arg]
        return self._instruments

    async def apply(self, diff: InstrumentDiff, at: object) -> None:
        raise AssertionError("the cache never writes")


def test_resolves_by_token_symbol_and_id() -> None:
    reliance = instrument("2885", tradingsymbol="RELIANCE-EQ")
    cache = InstrumentCache([reliance, instrument("1")])

    assert cache.by_token(Exchange.NSE, "2885") is reliance
    assert cache.by_symbol(Exchange.NSE, "RELIANCE-EQ") is reliance
    assert cache.by_id("NSE:2885") is reliance
    assert len(cache) == 2


def test_the_same_token_on_two_exchanges_resolves_separately() -> None:
    nse = instrument("500", exchange=Exchange.NSE)
    bse = instrument("500", exchange=Exchange.BSE, tradingsymbol="OTHER")
    cache = InstrumentCache([nse, bse])

    assert cache.by_token(Exchange.NSE, "500") is nse
    assert cache.by_token(Exchange.BSE, "500") is bse


@pytest.mark.parametrize(
    "lookup",
    [
        lambda c: c.by_token(Exchange.NSE, "nope"),
        lambda c: c.by_symbol(Exchange.NSE, "NOPE-EQ"),
        lambda c: c.by_id("NSE:nope"),
        lambda c: c.by_symbol(Exchange.BSE, "SYM1-EQ"),
    ],
)
def test_an_unknown_lookup_raises_a_typed_error(lookup: object) -> None:
    cache = InstrumentCache([instrument("1")])

    with pytest.raises(UnknownInstrumentError):
        lookup(cache)  # type: ignore[operator]


def test_refresh_replaces_the_whole_index_without_a_restart() -> None:
    cache = InstrumentCache([instrument("1")])
    renamed = instrument("1", tradingsymbol="RENAMED-EQ")

    cache.refresh([renamed, instrument("2")])

    assert cache.by_symbol(Exchange.NSE, "RENAMED-EQ") is renamed
    assert len(cache) == 2
    with pytest.raises(UnknownInstrumentError):
        cache.by_symbol(Exchange.NSE, "SYM1-EQ")


async def test_load_from_populates_from_the_persisted_master() -> None:
    cache = InstrumentCache()

    await cache.load_from(StubStore([instrument("7")]))

    assert cache.by_token(Exchange.NSE, "7").token == "7"

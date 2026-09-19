import pytest

from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money


def _instrument(**overrides: object) -> Instrument:
    fields: dict[str, object] = {
        "exchange": Exchange.NSE,
        "token": "10099",
        "tradingsymbol": "GODREJCP-EQ",
        "name": "GODREJCP",
        "lot_size": 1,
        "tick_size": Money.of("0.05"),
    }
    return Instrument(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_instrument_id_is_the_exchange_and_token() -> None:
    assert _instrument().instrument_id == "NSE:10099"


@pytest.mark.parametrize("field", ["token", "tradingsymbol"])
def test_token_and_symbol_are_required(field: str) -> None:
    with pytest.raises(ValueError, match="token and a tradingsymbol"):
        _instrument(**{field: ""})


def test_lot_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="lot size"):
        _instrument(lot_size=0)


@pytest.mark.parametrize("tick", ["0", "-0.05"])
def test_tick_size_must_be_positive(tick: str) -> None:
    with pytest.raises(ValueError, match="tick size"):
        _instrument(tick_size=Money.of(tick))

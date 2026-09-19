"""EM-60: the `Broker` ABC has exactly plan.md §5's interface, and no SmartAPI concept leaks."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import ModuleType

import pytest

from emporos.broker import base, models
from emporos.broker.base import Broker

PLAN_METHODS = {
    # session
    "authenticate", "ensure_session", "logout", "get_profile",
    # reference data
    "get_instruments",
    # market data
    "get_quote", "get_historical_candles", "subscribe_market_data", "unsubscribe_market_data",
    "on_tick",
    # orders
    "place_order", "modify_order", "cancel_order", "get_order_book", "get_trade_book",
    "find_orders_by_tag", "on_order_update",
    # account
    "get_positions", "get_holdings", "get_funds",
}  # fmt: skip
SYNC_METHODS = {"on_tick", "on_order_update"}  # handler registration; everything else awaits I/O


def test_the_interface_is_exactly_the_plans() -> None:
    assert set(Broker.__abstractmethods__) == PLAN_METHODS


def test_the_abstract_class_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError, match="abstract"):
        Broker()  # type: ignore[abstract]


def test_io_methods_are_coroutines_and_handler_registration_is_synchronous() -> None:
    for name in PLAN_METHODS:
        method = getattr(Broker, name)
        assert inspect.iscoroutinefunction(method) is (name not in SYNC_METHODS), name


def test_every_method_is_fully_annotated() -> None:
    for name in PLAN_METHODS:
        signature = inspect.signature(getattr(Broker, name))
        assert signature.return_annotation is not inspect.Signature.empty, name
        for parameter in list(signature.parameters.values())[1:]:
            assert parameter.annotation is not inspect.Parameter.empty, f"{name}.{parameter.name}"


def test_a_partial_implementation_is_still_abstract() -> None:
    class Half(Broker):
        async def authenticate(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    with pytest.raises(TypeError, match="abstract"):
        Half()  # type: ignore[abstract]


def _source(module: object) -> str:
    assert isinstance(module, ModuleType)
    return Path(inspect.getsourcefile(module) or "").read_text(encoding="utf-8")


# Words that belong to one broker's wire format. The neutral layer must speak domain vocabulary.
SMARTAPI_VOCABULARY = [
    "angelone", "angel one", "smartapi", "symboltoken", "ordertag", "transactiontype",
    "jwt", "feedtoken", "feed_token", "refreshtoken", "x-privatekey",
]  # fmt: skip


@pytest.mark.parametrize("module", [base, models])
def test_no_smartapi_concept_appears_in_the_neutral_layer(module: object) -> None:
    source = Path(inspect.getsourcefile(module) or "").read_text(encoding="utf-8").lower()  # type: ignore[arg-type]
    leaked = [
        w for w in SMARTAPI_VOCABULARY if re.search(rf"(?<![a-z_]){re.escape(w)}(?![a-z_])", source)
    ]
    assert leaked == [], f"broker-specific vocabulary in {module.__name__}: {leaked}"  # type: ignore[attr-defined]


def test_the_neutral_modules_import_no_broker_adapter() -> None:
    for module in (base, models):
        source = _source(module)
        assert "broker.angelone" not in source and "SmartApi" not in source

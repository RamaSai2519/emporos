"""No script or live test can place, modify or cancel an order (the dev key is not IP-registered,
and nothing in this repository may attempt a real order from it).

This is a structural guard: it scans the places that talk to the real broker for any reference to
the order-write endpoints or the broker methods that reach them."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r"PLACE_ORDER|MODIFY_ORDER|CANCEL_ORDER|\.place_order\(|\.modify_order\(|\.cancel_order\(|"
    r"placeOrder|modifyOrder|cancelOrder"
)


def live_surface() -> list[Path]:
    return [
        *(REPO / "scripts").glob("*.py"),
        *(REPO / "tests" / "integration").rglob("*.py"),
    ]


def test_the_places_that_reach_the_real_broker_never_mention_order_writes() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in live_surface()
        if FORBIDDEN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_guard_actually_sees_the_files_it_is_meant_to_watch() -> None:
    names = {p.name for p in live_surface()}
    assert "record_angelone_fixtures.py" in names and "test_angelone_live_reads.py" in names


def test_only_the_adapter_layer_defines_or_calls_the_order_write_endpoints() -> None:
    allowed = {"endpoints.py", "api.py", "adapter.py", "mapping.py", "base.py"}
    call = re.compile(r"Endpoints\.(PLACE|MODIFY|CANCEL)_ORDER|\.place_order\(")
    users = {p.name for p in (REPO / "src").rglob("*.py") if call.search(p.read_text())}
    assert users <= allowed, users - allowed

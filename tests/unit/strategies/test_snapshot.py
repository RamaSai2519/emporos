from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import pytest

from emporos.strategies.resolution import StrategyConfigError, StrategyConfigResolver
from emporos.strategies.snapshot import (
    Canonicalizer,
    ConfigSnapshotter,
    SnapshotIntegrityError,
)
from tests.support.strategies import (
    INSTRUMENT_MASTER,
    changed,
    raw_config,
    threshold_registry,
)


def _config(raw: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    return StrategyConfigResolver(threshold_registry(), INSTRUMENT_MASTER).resolve(
        raw or raw_config()
    )


def test_the_snapshot_is_canonical_json_with_no_floats() -> None:
    snapshot = ConfigSnapshotter().take(_config())

    document = snapshot.document
    assert json.loads(json.dumps(document)) == document  # survives a JSON round trip unchanged
    assert document["schema_version"] == 1 and document["timeframe"] == "5m"
    assert document["session"] == {"no_new_entries_after": "15:00", "square_off_at": "15:15"}
    assert document["parameters"] == {"threshold": "100", "quantity": 10}
    assert document["risk"] == {
        "max_position_value": "50000",
        "max_open_positions": 3,
        "stop_loss_pct": "1",
        "target_pct": "2",
    }
    assert document["universe"] == [
        {"symbol": "NSE:ALPHA-EQ", "instrument_id": "NSE:1001"},
        {"symbol": "NSE:BETA-EQ", "instrument_id": "NSE:1002"},
    ]


def test_the_hash_is_sha256_over_compact_key_sorted_json() -> None:
    """Pinned to the spec, so a future change to how it is computed cannot go unnoticed."""
    snapshot = ConfigSnapshotter().take(_config())

    text = json.dumps(snapshot.document, sort_keys=True, separators=(",", ":"))
    assert snapshot.content_hash == "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_the_same_config_always_hashes_the_same() -> None:
    assert ConfigSnapshotter().take(_config()).content_hash == (
        ConfigSnapshotter().take(_config()).content_hash
    )


def test_the_hash_ignores_how_the_yaml_was_written() -> None:
    reordered = dict(reversed(list(raw_config().items())))
    spelled_differently = changed(raw_config(), "risk.stop_loss_pct", "1.000")
    spelled_differently = changed(spelled_differently, "risk.target_pct", 2)

    baseline = ConfigSnapshotter().take(_config()).content_hash
    assert ConfigSnapshotter().take(_config(reordered)).content_hash == baseline
    assert ConfigSnapshotter().take(_config(spelled_differently)).content_hash == baseline


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("enabled", False),
        ("timeframe", "15m"),
        ("universe.instruments", ["NSE:ALPHA-EQ"]),
        ("parameters.threshold", "101"),
        ("parameters.quantity", 11),
        ("risk.max_position_value", 50001),
        ("risk.max_open_positions", 4),
        ("risk.stop_loss_pct", "1.5"),
        ("risk.target_pct", "2.5"),
        ("execution.limit_buffer_bps", 6),
        ("execution.reprice_after_seconds", 31),
        ("execution.max_reprices", 4),
        ("session.no_new_entries_after", "14:59"),
        ("session.square_off_at", "15:16"),
    ],
)
def test_any_change_to_the_config_changes_the_hash(path: str, value: object) -> None:
    baseline = ConfigSnapshotter().take(_config()).content_hash
    assert ConfigSnapshotter().take(_config(changed(raw_config(), path, value))).content_hash != (
        baseline
    )


def test_a_snapshot_restores_to_an_identical_config() -> None:
    snapshotter = ConfigSnapshotter()
    original = _config()
    snapshot = snapshotter.take(original)

    restored = snapshotter.restore(snapshot.document, threshold_registry(), snapshot.content_hash)

    assert restored == original
    assert snapshotter.take(restored) == snapshot  # and snapshots again to the same hash


def test_a_snapshot_survives_being_stored_and_read_back_as_json() -> None:
    snapshotter = ConfigSnapshotter()
    original = _config()
    snapshot = snapshotter.take(original)

    reloaded = json.loads(json.dumps(snapshot.document))

    assert snapshotter.restore(reloaded, threshold_registry(), snapshot.content_hash) == original


def test_restoring_needs_no_yaml_and_no_instrument_master() -> None:
    """The snapshot alone reproduces the config: symbols and tokens were frozen into it."""
    snapshot = ConfigSnapshotter().take(_config())
    restored = ConfigSnapshotter().restore(snapshot.document, threshold_registry())
    assert restored.instrument_ids == ("NSE:1001", "NSE:1002")


def test_a_tampered_snapshot_is_refused() -> None:
    snapshot = ConfigSnapshotter().take(_config())
    tampered = json.loads(json.dumps(snapshot.document))
    tampered["risk"]["max_position_value"] = "5000000"

    with pytest.raises(SnapshotIntegrityError):
        ConfigSnapshotter().restore(tampered, threshold_registry(), snapshot.content_hash)


def test_an_unknown_schema_version_is_refused() -> None:
    document = dict(ConfigSnapshotter().take(_config()).document) | {"schema_version": 99}
    with pytest.raises(StrategyConfigError, match="schema"):
        ConfigSnapshotter().restore(document, threshold_registry())


def test_a_snapshot_for_an_unregistered_strategy_cannot_be_restored() -> None:
    document = dict(ConfigSnapshotter().take(_config()).document) | {"name": "gone"}
    with pytest.raises(StrategyConfigError, match="cannot be restored"):
        ConfigSnapshotter().restore(document, threshold_registry())


def test_a_snapshot_with_invalid_content_cannot_be_restored() -> None:
    document = json.loads(json.dumps(ConfigSnapshotter().take(_config()).document))
    document["risk"]["stop_loss_pct"] = "0"
    with pytest.raises(StrategyConfigError, match="cannot be restored"):
        ConfigSnapshotter().restore(document, threshold_registry())


def test_a_float_can_never_enter_a_snapshot() -> None:
    document = dict(ConfigSnapshotter().take(_config()).document) | {"extra": 1.5}
    with pytest.raises(StrategyConfigError, match="float"):
        ConfigSnapshotter().restore(document, threshold_registry())


class TestCanonicalizer:
    def test_decimals_are_normalised_strings(self) -> None:
        canonical = Canonicalizer().canonical
        assert canonical(Decimal("1.50")) == "1.5"
        assert canonical(Decimal("50000")) == "50000"
        assert canonical(Decimal("5E+4")) == "50000"
        assert canonical(Decimal("0.00")) == "0"
        assert canonical(Decimal("-0.0")) == "0"

    def test_containers_are_converted_recursively(self) -> None:
        canonical = Canonicalizer().canonical
        assert canonical({"a": (1, Decimal("2.0")), 3: None}) == {"a": [1, "2"], "3": None}

    def test_bools_stay_bools_and_floats_and_strangers_are_refused(self) -> None:
        canonical = Canonicalizer().canonical
        assert canonical(True) is True
        with pytest.raises(StrategyConfigError, match="float"):
            canonical({"a": [0.5]})
        with pytest.raises(StrategyConfigError, match="cannot snapshot"):
            canonical(object())

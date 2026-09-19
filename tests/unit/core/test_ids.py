from emporos.core.ids import new_correlation_id, new_idempotency_key, new_ulid


def test_new_ulid_is_26_char_crockford_base32() -> None:
    value = new_ulid()
    assert len(value) == 26
    assert value == value.upper()


def test_new_ulid_is_unique() -> None:
    assert new_ulid() != new_ulid()


def test_ulid_prefix_encodes_millisecond_timestamp_for_sortability() -> None:
    # The first 10 characters are the millisecond timestamp — this is what makes
    # ULIDs sort by creation time; the trailing 16 are random and not ordered
    # within the same millisecond.
    from ulid import ULID

    value = new_ulid()
    decoded = ULID.from_str(value)
    assert decoded.timestamp > 0


def test_new_correlation_id_is_a_ulid() -> None:
    assert len(new_correlation_id()) == 26


def test_new_idempotency_key_is_namespaced() -> None:
    key = new_idempotency_key("order")
    assert key.startswith("order-")
    assert len(key) == len("order-") + 26

from pymongo.errors import DuplicateKeyError

from emporos.core.errors import ErrorClassification
from emporos.persistence.errors import DuplicateKeyTranslator


def test_translates_a_driver_duplicate_key_error_into_a_typed_error() -> None:
    driver_error = DuplicateKeyError(
        "E11000 duplicate key error collection: emporos_dev.orders index: idempotency_key_1",
        11000,
        {"keyPattern": {"idempotency_key": 1}, "errmsg": "... index: idempotency_key_1 dup key"},
    )

    error = DuplicateKeyTranslator().translate("orders", driver_error)

    assert error.collection == "orders"
    assert error.index_name == "idempotency_key_1"
    assert error.key_fields == ("idempotency_key",)
    assert error.classification is ErrorClassification.DEFINITIVE


def test_translation_tolerates_a_driver_error_without_details() -> None:
    error = DuplicateKeyTranslator().translate("orders", DuplicateKeyError("boom"))

    assert error.index_name == "unknown"
    assert error.key_fields == ()

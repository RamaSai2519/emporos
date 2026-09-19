from emporos.core.errors import (
    AmbiguousError,
    DefinitiveError,
    ErrorClassification,
    RetryableError,
)


def test_retryable_error_classification() -> None:
    err = RetryableError("timeout before dispatch")
    assert err.classification is ErrorClassification.RETRYABLE


def test_definitive_error_classification() -> None:
    err = DefinitiveError("validation failed")
    assert err.classification is ErrorClassification.DEFINITIVE


def test_ambiguous_error_classification() -> None:
    err = AmbiguousError("connection reset mid-request")
    assert err.classification is ErrorClassification.AMBIGUOUS


def test_classification_overridable_per_instance() -> None:
    err = DefinitiveError("odd case", classification=ErrorClassification.AMBIGUOUS)
    assert err.classification is ErrorClassification.AMBIGUOUS


def test_message_preserved() -> None:
    err = RetryableError("rate limited")
    assert err.message == "rate limited"
    assert str(err) == "rate limited"

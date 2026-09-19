from collections.abc import Iterator

import pytest

from emporos.core.config import Settings


@pytest.fixture(autouse=True)
def _isolated_default_settings() -> Iterator[None]:
    """`Settings.default()` is process-cached; never let one test's env leak into another's."""
    Settings.default.cache_clear()
    yield
    Settings.default.cache_clear()

import pytest

from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.persistence.mongo import MongoClientFactory


def test_mongo_client_factory_without_mongo_url_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONGO_URL", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(ConfigurationError):
        MongoClientFactory(settings)

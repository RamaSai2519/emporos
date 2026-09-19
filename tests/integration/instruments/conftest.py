from collections.abc import AsyncIterator

import pytest

from emporos.core.config import Settings
from tests.support.mongo_instruments import Rig, scratch_rig


@pytest.fixture
async def rig(dev_settings: Settings) -> AsyncIterator[Rig]:
    async for scratch in scratch_rig(dev_settings):
        yield scratch

from __future__ import annotations

from emporos.core.errors import DefinitiveError


class ConfigDrift(DefinitiveError):
    """A paper run's recorded config cannot be reproduced exactly. Comparing a backtest of a
    DIFFERENT config against it would be meaningless, so the comparison is refused."""

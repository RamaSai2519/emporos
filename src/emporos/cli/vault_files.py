"""Reads the vault's committed state: the seal and the unseal records (EM-191 §4.3).

Both live under `docs/research/edge-search/` as YAML. An unseal record only counts if git tracks it
with no pending change, the same declared-before-use proof `DeclarationGate` demands of an
experiment: an open that can still be edited, or that exists only in a working tree, is not an
open. The gate that results is built once per composition root and shared by every reader in it.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from emporos.backtest.vault import MAX_OPENS, UnsealRecord, VaultGate, VaultSeal
from emporos.cli.experiment_provenance import GitRepository
from emporos.core.errors import ConfigurationError

__all__ = ["DEFAULT_OPENS_DIR", "DEFAULT_SEAL_FILE", "VaultFiles"]

VAULT_DIR = Path("docs/research/edge-search")
DEFAULT_SEAL_FILE = VAULT_DIR / "vault.yaml"
DEFAULT_OPENS_DIR = VAULT_DIR / "vault-opens"

_SEAL_KEYS = {"first_day", "last_day", "instruments", "max_opens"}
_OPEN_KEYS = {
    "open_number", "seal_hash", "candidate_hash", "reason", "opened_at", "first_day", "last_day",
    "instruments",
}  # fmt: skip


class VaultFiles:
    def __init__(
        self,
        seal_file: Path = DEFAULT_SEAL_FILE,
        opens_dir: Path = DEFAULT_OPENS_DIR,
        git: GitRepository | None = None,
    ) -> None:
        self._seal_file = seal_file
        self._opens_dir = opens_dir
        self._git = git or GitRepository()

    def load(self) -> VaultGate:
        """The gate. A missing seal is an error, never an open vault: nothing here fails open."""
        seal = self.load_seal()
        opens = [self._open(path) for path in sorted(self._opens_dir.glob("*.yaml"))]
        return VaultGate(seal, opens)

    def load_seal(self) -> VaultSeal:
        document = self._read(self._seal_file, _SEAL_KEYS)
        try:
            return VaultSeal(
                self._day(document, "first_day", self._seal_file),
                self._day(document, "last_day", self._seal_file),
                self._instruments(document["instruments"], self._seal_file),
                int(document.get("max_opens", MAX_OPENS)),
            )
        except (ValueError, TypeError) as error:
            raise ConfigurationError(f"{self._seal_file}: {error}") from error

    def _open(self, path: Path) -> UnsealRecord:
        if not self._git.is_committed(path):
            raise ConfigurationError(
                f"{path} is not committed: an unseal record counts only once it is in git"
            )
        document = self._read(path, _OPEN_KEYS)
        try:
            opened_at = document["opened_at"]
            if not isinstance(opened_at, datetime):
                opened_at = datetime.fromisoformat(str(opened_at))
            return UnsealRecord(
                int(document["open_number"]),
                str(document["seal_hash"]),
                str(document["candidate_hash"]),
                str(document["reason"]),
                opened_at,
                self._day(document, "first_day", path),
                self._day(document, "last_day", path),
                self._instruments(document["instruments"], path),
            )
        except (ValueError, TypeError) as error:
            raise ConfigurationError(f"{path}: {error}") from error

    @staticmethod
    def _read(path: Path, keys: set[str]) -> dict[str, Any]:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{path} must be a mapping")
        unknown = sorted(set(document) - keys)
        if unknown:
            raise ConfigurationError(f"{path}: unknown key(s) {', '.join(unknown)}")
        required = keys - {"max_opens"}
        missing = sorted(required - set(document))
        if missing:
            raise ConfigurationError(f"{path}: missing {', '.join(missing)}")
        return document

    @staticmethod
    def _day(document: dict[str, Any], key: str, path: Path) -> date:
        value: object = document[key]
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        raise ConfigurationError(f"{path}: {key} must be a date like 2026-03-19")

    @staticmethod
    def _instruments(value: object, path: Path) -> frozenset[str] | None:
        if value == "all":
            return None
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return frozenset(value)
        raise ConfigurationError(f"{path}: instruments must be 'all' or a non-empty list of ids")

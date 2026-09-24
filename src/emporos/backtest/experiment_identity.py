"""Mints the stable id of an experiment from its declaration (EM-188).

The domain owns the id's shape but imports nothing from `core`, so the content hash is taken here:
the same declaration always gets the same id, whenever and wherever it is run."""

from __future__ import annotations

from emporos.core.hashing import HASH_PREFIX, Canonical, content_hash
from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentId

_DIGEST_LENGTH = 8


class ExperimentIdMinter:
    def mint(self, declaration: ExperimentDeclaration) -> ExperimentId:
        document: dict[str, Canonical] = dict(declaration.canonical())
        digest = content_hash(document).removeprefix(HASH_PREFIX)[:_DIGEST_LENGTH]
        return ExperimentId.of(declaration.declared_at.date(), declaration.slug, digest)

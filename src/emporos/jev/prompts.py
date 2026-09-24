"""`JevPrompt` — the system prompt Jev is asked with, as a versioned, content-hashed value.

An experiment that cannot say which prompt produced its decisions cannot be reproduced or
audited: a one-word edit to the instruction changes every answer. So the prompt is a value, not
a module constant buried in the client: it carries a human `version` and a SHA-256 of its text
computed at construction, and every `JevDecision` records both. Changing the text without
bumping the version is not detected here (the hash still changes, which is what the journal and
the trial ledger key on), but the hash makes the change visible everywhere it matters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class JevPrompt:
    version: str
    system_text: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("a Jev prompt needs a version")
        if not self.system_text.strip():
            raise ValueError("a Jev prompt needs text")
        digest = hashlib.sha256(self.system_text.encode("utf-8")).hexdigest()
        object.__setattr__(self, "content_hash", digest)


DEFAULT_PROMPT = JevPrompt(
    version="v1",
    system_text=(
        "You are a trading decision-support filter. Given structured context about a candidate "
        "trade opportunity, respond with ONLY a JSON object of the form "
        '{"decision": "confirm"|"reject"|"abstain", "confidence": <0..1>, '
        '"reason": "<short text>"}. No other text.'
    ),
)

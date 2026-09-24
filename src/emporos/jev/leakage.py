"""Look-ahead controls for a Jev experiment (EM-187).

An LLM's pretrained knowledge is look-ahead for any bar before its training cutoff: it may
"remember" how a stock traded on a given day, which would make a backtest of Jev measure recall,
not judgement. Two independent defences, both on by default in an experiment:

* `KnowledgeCutoffGuard` refuses any window (and any single request) that is not strictly after
  the model's declared knowledge cutoff plus a safety margin. The cutoff is never assumed: an
  experiment must DECLARE it, with the source it was verified against, and a missing value is a
  refusal, not a default.
* `SymbolAnonymiser` replaces the instrument with a stable, salted pseudonym and strips
  identifying keys before a request leaves the process, so even a window the guard let through
  cannot be answered from memory of a named stock.

Both fail LOUDLY with `JevLeakageError`. A leak is not an ABSTAIN: swallowing it as one would let
a contaminated experiment finish and look valid.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from emporos.core.errors import ConfigurationError
from emporos.jev.config import JevConfig
from emporos.jev.models import JevDecision, JevRequest
from emporos.jev.protocol import JevProvider


class JevLeakageError(ConfigurationError):
    """The experiment as configured could let the model know the future or the instrument."""


DEFAULT_CUTOFF_MARGIN = timedelta(days=90)

# Keys that name the instrument or a moment, matched case-insensitively and exactly. A request's
# own fields are already a closed set; this list guards the free-form mappings a caller may add.
DEFAULT_DENY_LIST = frozenset(
    {
        "symbol",
        "ticker",
        "isin",
        "instrument",
        "instrument_id",
        "company",
        "company_name",
        "name",
        "date",
        "datetime",
        "timestamp",
        "as_of",
        "time",
    }
)


class KnowledgeCutoffGuard:
    def __init__(self, margin: timedelta = DEFAULT_CUTOFF_MARGIN) -> None:
        if margin < timedelta(0):
            raise ValueError("the cutoff margin cannot be negative")
        self._margin = margin

    def earliest_allowed(self, config: JevConfig) -> date:
        """The first day an experiment may use: strictly after cutoff + margin."""
        return self._cutoff(config) + self._margin + timedelta(days=1)

    def check(self, window_start: date, config: JevConfig) -> None:
        """Every experiment window, training windows included, must start after the cutoff."""
        earliest = self.earliest_allowed(config)
        if window_start < earliest:
            raise JevLeakageError(
                f"the window starts {window_start}, but the model's knowledge cutoff "
                f"{self._cutoff(config)} plus a {self._margin.days}-day margin means nothing "
                f"before {earliest} is safe: the model may remember it"
            )

    def check_as_of(self, as_of: datetime, config: JevConfig) -> None:
        self.check(as_of.date(), config)

    @staticmethod
    def _cutoff(config: JevConfig) -> date:
        if config.model_knowledge_cutoff is None:
            raise JevLeakageError(
                "no model knowledge cutoff is declared: a Jev experiment must state the model's "
                "cutoff, and where it was verified, before it may run"
            )
        return config.model_knowledge_cutoff


class CutoffEnforcingJevProvider:
    """A `JevProvider` decorator that refuses any request from before the safe date, so a window
    that slipped past the up-front check (a walk-forward fold, a replayed journal) still cannot
    reach the model or its recorded answers."""

    def __init__(self, inner: JevProvider, guard: KnowledgeCutoffGuard, config: JevConfig) -> None:
        self._inner = inner
        self._guard = guard
        self._config = config

    async def decide(self, request: JevRequest) -> JevDecision:
        self._guard.check_as_of(request.as_of, self._config)
        return await self._inner.decide(request)


class SymbolAnonymiser:
    """A `JevProvider` decorator: what the wrapped provider sees is the same request with the
    instrument replaced by `INSTR_<hash>` and identifying keys removed. The pseudonym is an
    HMAC of the symbol under the run `salt`, so it is stable within a run (and across a replay
    that reuses the salt) yet unguessable without it."""

    def __init__(
        self,
        inner: JevProvider,
        salt: str,
        deny_list: frozenset[str] = DEFAULT_DENY_LIST,
    ) -> None:
        if not salt:
            raise ValueError("an anonymiser needs a run salt")
        self._inner = inner
        self._salt = salt.encode("utf-8")
        self._deny = frozenset(key.lower() for key in deny_list)

    def pseudonym(self, symbol: str) -> str:
        digest = hmac.new(self._salt, symbol.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"INSTR_{digest[:8]}"

    def anonymise(self, request: JevRequest) -> JevRequest:
        cleaned = replace(
            request,
            symbol=self.pseudonym(request.symbol),
            features=self._stripped(request.features, request.symbol),
            historical_conditional_performance=self._stripped(
                request.historical_conditional_performance, request.symbol
            ),
            portfolio_context=self._stripped(request.portfolio_context, request.symbol),
        )
        self._assert_no_identity(cleaned, request.symbol)
        return cleaned

    async def decide(self, request: JevRequest) -> JevDecision:
        return await self._inner.decide(self.anonymise(request))

    def _stripped(self, values: Mapping[str, Decimal], symbol: str) -> dict[str, Decimal]:
        names = _identifying_names(symbol)
        return {
            key: value
            for key, value in values.items()
            if key.lower() not in self._deny and not _mentions(key, names)
        }

    @staticmethod
    def _assert_no_identity(request: JevRequest, symbol: str) -> None:
        """Defence in depth: whatever the deny-list missed, the real name must not be anywhere in
        what would be sent."""
        names = _identifying_names(symbol)
        if _mentions(repr(_strings(request.payload())), names):
            raise JevLeakageError(
                "an instrument name survived anonymisation in the outgoing Jev request"
            )


def _identifying_names(symbol: str) -> tuple[str, ...]:
    """`NSE:RELIANCE-EQ` is identified by the whole id and by the bare `RELIANCE`. A bare token
    that is only digits (the `NSE:2885` form a resolved instrument id takes) names nothing on its
    own and would match every price containing those digits, so only the whole id is checked."""
    bare = re.sub(r"^[A-Za-z]+:", "", symbol)
    bare = re.sub(r"-[A-Za-z]{1,3}$", "", bare)
    names = {symbol} | (set() if bare.isdigit() else {bare})
    return tuple(name.lower() for name in names if len(name) >= 2)


def _mentions(text: str, names: tuple[str, ...]) -> bool:
    """Whole-token match, so a short ticker does not trip on an unrelated word containing it."""
    lowered = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", lowered) for name in names)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, inner in value.items():
            found.append(str(key))
            found.extend(_strings(inner))
        return found
    return []

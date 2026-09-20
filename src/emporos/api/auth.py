"""Single-operator authentication (plan.md §15.3): passcode, Argon2id hash, signed short-lived JWT.

This is a private, single-operator system, so the design is deliberately small:

* The passcode is stored ONLY as an Argon2id hash, in MongoDB — never plaintext, never in Git or
  the environment. Verification is constant-time, and runs against a dummy hash when no passcode is
  set so "no passcode configured" is not distinguishable by timing.
* A correct passcode earns an HS256 JWT valid for hours. Verification pins the algorithm (a token
  claiming `alg: none`, or any other, is refused), requires `exp`, `iat` and `sub`, and checks
  expiry against the injected clock.
* Login attempts are throttled per client, so the passcode cannot be guessed by hammering the route.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from emporos.core.clock import Clock

_ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32


class InvalidTokenError(Exception):
    """The bearer token is malformed, tampered with, expired or not ours."""


class LoginThrottledError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"too many attempts; retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class InvalidPasscodeError(Exception):
    """The passcode was wrong. Deliberately says nothing else."""


class PasscodeStore(Protocol):
    async def passcode_hash(self) -> str | None: ...

    async def set_passcode_hash(self, hashed: str) -> None: ...


class Argon2Passcodes:
    """Hash and verify passcodes with Argon2id (the `argon2-cffi` defaults are Argon2id)."""

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher()
        self._dummy = self._hasher.hash("not-the-passcode")  # so a missing hash costs the same

    def hash(self, passcode: str) -> str:
        if len(passcode) < 8:
            raise ValueError("a passcode needs at least 8 characters")
        return self._hasher.hash(passcode)

    def verify(self, hashed: str | None, passcode: str) -> bool:
        try:
            self._hasher.verify(hashed or self._dummy, passcode)
        except (VerificationError, InvalidHashError):
            return False
        return hashed is not None


@dataclass(frozen=True)
class Token:
    value: str
    expires_at: datetime


@dataclass(frozen=True)
class Claims:
    subject: str
    expires_at: datetime


class TokenService:
    def __init__(self, secret: bytes, clock: Clock, ttl: timedelta = timedelta(hours=12)) -> None:
        if len(secret) < MIN_SECRET_BYTES:
            raise ValueError(f"the signing secret needs at least {MIN_SECRET_BYTES} bytes")
        self._secret = secret
        self._clock = clock
        self._ttl = ttl

    def issue(self, subject: str) -> Token:
        now = self._clock.now()
        expires = now + self._ttl
        value = jwt.encode(
            {"sub": subject, "iat": int(now.timestamp()), "exp": int(expires.timestamp())},
            self._secret,
            algorithm=_ALGORITHM,
        )
        return Token(value, expires)

    def verify(self, value: str) -> Claims:
        try:
            claims: dict[str, Any] = jwt.decode(
                value,
                self._secret,
                algorithms=[_ALGORITHM],  # pinned: never trust the token's own `alg`
                options={"require": ["exp", "iat", "sub"], "verify_exp": False},
            )
        except jwt.PyJWTError as error:
            raise InvalidTokenError("invalid token") from error
        expires = datetime.fromtimestamp(int(claims["exp"]), tz=self._clock.now().tzinfo)
        if expires <= self._clock.now():  # expiry against OUR clock, not the wall clock
            raise InvalidTokenError("token expired")
        return Claims(str(claims["sub"]), expires)


class LoginThrottle:
    """At most `max_attempts` login attempts per `window` per client; a success clears the count."""

    def __init__(
        self, clock: Clock, max_attempts: int = 5, window: timedelta = timedelta(minutes=1)
    ) -> None:
        if max_attempts < 1 or window <= timedelta(0):
            raise ValueError("a throttle needs a positive limit and window")
        self._clock = clock
        self._max = max_attempts
        self._window = window
        self._attempts: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def check(self, client: str) -> None:
        """Raise if `client` has used up its attempts; otherwise count this one."""
        now = self._clock.now()
        attempts = self._attempts[client]
        while attempts and now - attempts[0] >= self._window:
            attempts.popleft()
        if len(attempts) >= self._max:
            raise LoginThrottledError((attempts[0] + self._window - now).total_seconds())
        attempts.append(now)

    def succeeded(self, client: str) -> None:
        self._attempts.pop(client, None)


class AuthService:
    SUBJECT = "operator"

    def __init__(
        self,
        store: PasscodeStore,
        passcodes: Argon2Passcodes,
        tokens: TokenService,
        throttle: LoginThrottle,
    ) -> None:
        self._store = store
        self._passcodes = passcodes
        self._tokens = tokens
        self._throttle = throttle

    async def login(self, passcode: str, client: str) -> Token:
        self._throttle.check(client)
        stored = await self._store.passcode_hash()
        if not self._passcodes.verify(stored, passcode):
            raise InvalidPasscodeError
        self._throttle.succeeded(client)
        return self._tokens.issue(self.SUBJECT)

    def authenticate(self, token: str) -> Claims:
        return self._tokens.verify(token)

    async def set_passcode(self, passcode: str) -> None:
        await self._store.set_passcode_hash(self._passcodes.hash(passcode))

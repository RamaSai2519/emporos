"""Single-passcode login: hashed, constant-time, throttled, and a token that cannot be forged."""

import time
from datetime import timedelta

import jwt
import pytest

from emporos.api.auth import (
    Argon2Passcodes,
    AuthService,
    InvalidPasscodeError,
    InvalidTokenError,
    LoginThrottle,
    LoginThrottledError,
    TokenService,
)
from emporos.core.clock import FixedClock
from tests.support.records import NOW

SECRET = b"s" * 32


class Store:
    def __init__(self, hashed: str | None = None) -> None:
        self.hashed = hashed

    async def passcode_hash(self) -> str | None:
        return self.hashed

    async def set_passcode_hash(self, hashed: str) -> None:
        self.hashed = hashed


@pytest.fixture(scope="module")
def passcodes() -> Argon2Passcodes:
    return Argon2Passcodes()


def service(
    passcodes: Argon2Passcodes, store: Store, clock: FixedClock | None = None, **kw: object
) -> AuthService:
    clock = clock or FixedClock(NOW)
    return AuthService(store, passcodes, TokenService(SECRET, clock), LoginThrottle(clock, **kw))  # type: ignore[arg-type]


class TestPasscodes:
    def test_the_hash_is_argon2id_never_the_passcode_and_verifies_only_the_right_one(
        self, passcodes: Argon2Passcodes
    ) -> None:
        hashed = passcodes.hash("correct horse battery")
        assert hashed.startswith("$argon2id$") and "correct horse" not in hashed
        assert passcodes.verify(hashed, "correct horse battery")
        assert not passcodes.verify(hashed, "correct horse batterY")
        assert not passcodes.verify(hashed, "")

    def test_a_passcode_must_be_long_enough(self, passcodes: Argon2Passcodes) -> None:
        with pytest.raises(ValueError, match="8 characters"):
            passcodes.hash("short")

    def test_no_passcode_set_never_verifies_and_garbage_hashes_never_verify(
        self, passcodes: Argon2Passcodes
    ) -> None:
        assert not passcodes.verify(None, "anything at all")
        assert not passcodes.verify("not a hash", "anything at all")


class TestLogin:
    async def test_the_right_passcode_earns_a_token_the_service_accepts(
        self, passcodes: Argon2Passcodes
    ) -> None:
        store = Store()
        auth = service(passcodes, store)
        await auth.set_passcode("correct horse battery")
        assert store.hashed and store.hashed.startswith("$argon2id$")
        token = await auth.login("correct horse battery", "1.2.3.4")
        claims = auth.authenticate(token.value)
        assert claims.subject == "operator" and token.expires_at == NOW + timedelta(hours=12)

    async def test_a_wrong_passcode_or_no_passcode_set_is_rejected_identically(
        self, passcodes: Argon2Passcodes
    ) -> None:
        set_up = Store(passcodes.hash("correct horse battery"))
        for store in (set_up, Store(None)):
            with pytest.raises(InvalidPasscodeError):
                await service(passcodes, store).login("wrong passcode!", "1.2.3.4")

    async def test_repeated_attempts_are_throttled_per_client_and_recover(
        self, passcodes: Argon2Passcodes
    ) -> None:
        clock = FixedClock(NOW)
        auth = service(
            passcodes, Store(passcodes.hash("correct horse battery")), clock, max_attempts=3
        )
        for _ in range(3):
            with pytest.raises(InvalidPasscodeError):
                await auth.login("nope nope nope", "1.2.3.4")
        with pytest.raises(LoginThrottledError) as caught:  # even the RIGHT passcode is refused now
            await auth.login("correct horse battery", "1.2.3.4")
        assert 0 < caught.value.retry_after <= 60
        with pytest.raises(InvalidPasscodeError):  # another client is unaffected
            await auth.login("nope nope nope", "9.9.9.9")
        clock.advance(timedelta(seconds=61))
        assert (await auth.login("correct horse battery", "1.2.3.4")).value

    async def test_a_success_clears_the_clients_count(self, passcodes: Argon2Passcodes) -> None:
        auth = service(passcodes, Store(passcodes.hash("correct horse battery")), max_attempts=2)
        for _ in range(5):
            await auth.login("correct horse battery", "1.2.3.4")

    def test_a_throttle_needs_a_positive_limit(self) -> None:
        with pytest.raises(ValueError):
            LoginThrottle(FixedClock(NOW), max_attempts=0)


class TestTokens:
    clock = FixedClock(NOW)

    def tokens(
        self, clock: FixedClock | None = None, ttl: timedelta = timedelta(hours=12)
    ) -> TokenService:
        return TokenService(SECRET, clock or FixedClock(NOW), ttl)

    def test_a_token_expires_on_our_clock(self) -> None:
        clock = FixedClock(NOW)
        tokens = self.tokens(clock, timedelta(hours=1))
        token = tokens.issue("operator")
        clock.advance(timedelta(minutes=59))
        assert tokens.verify(token.value).subject == "operator"
        clock.advance(timedelta(minutes=1))
        with pytest.raises(InvalidTokenError, match="expired"):
            tokens.verify(token.value)

    def test_a_tampered_or_foreign_token_is_refused(self) -> None:
        tokens = self.tokens()
        good = tokens.issue("operator").value
        head, body, sig = good.split(".")
        for bad in (f"{head}.{body}.{sig[:-2]}xx", f"{head}.{body}x.{sig}", "garbage", ""):
            with pytest.raises(InvalidTokenError):
                tokens.verify(bad)
        other = TokenService(b"o" * 32, FixedClock(NOW)).issue("operator").value
        with pytest.raises(InvalidTokenError):
            tokens.verify(other)  # signed with a different secret

    def test_alg_none_and_other_algorithms_are_never_trusted(self) -> None:
        now = int(NOW.timestamp())
        claims = {"sub": "operator", "iat": now, "exp": now + 3600}
        unsigned = jwt.encode(claims, key="", algorithm="none")
        hs512 = jwt.encode(claims, SECRET, algorithm="HS512")
        for forged in (unsigned, hs512):
            with pytest.raises(InvalidTokenError):
                self.tokens().verify(forged)

    def test_a_token_without_the_required_claims_is_refused(self) -> None:
        now = int(NOW.timestamp())
        for claims in (
            {"sub": "x", "iat": now},
            {"exp": now + 60, "iat": now},
            {"sub": "x", "exp": now + 60},
        ):
            with pytest.raises(InvalidTokenError):
                self.tokens().verify(jwt.encode(claims, SECRET, algorithm="HS256"))

    def test_a_weak_secret_is_refused(self) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            TokenService(b"short", FixedClock(NOW))

    def test_wall_clock_never_decides_expiry(self) -> None:
        old = FixedClock(NOW)  # 2026-01-05: long before "real" now(2026-09) in wall-clock terms
        token = self.tokens(old, timedelta(hours=1)).issue("operator")
        assert self.tokens(old).verify(token.value) and time.time() > NOW.timestamp()

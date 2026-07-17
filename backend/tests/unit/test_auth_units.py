"""Unit tests for the auth building blocks: JWT, session rotation, encryption."""

from __future__ import annotations

import uuid
from collections import defaultdict

import pytest

from app.application.services.session_service import SessionService
from app.core.config import get_settings
from app.core.security import JWTService
from app.exceptions.base import AuthenticationError
from app.infrastructure.crypto.encryption import TokenEncryptor


class FakeStore:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = defaultdict(set)

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._kv[key] = value

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)

    async def add_member(self, key: str, member: str) -> None:
        self._sets[key].add(member)

    async def members(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def remove_member(self, key: str, member: str) -> None:
        self._sets[key].discard(member)


# --- JWT -------------------------------------------------------------------
def test_jwt_roundtrip() -> None:
    svc = JWTService(get_settings())
    uid = str(uuid.uuid4())
    token = svc.create_access_token(subject=uid, session_id="sid-1")
    claims = svc.decode_access_token(token)
    assert claims["sub"] == uid
    assert claims["sid"] == "sid-1"
    assert claims["type"] == "access"


def test_jwt_tampered_rejected() -> None:
    svc = JWTService(get_settings())
    token = svc.create_access_token(subject="u", session_id="s")
    with pytest.raises(AuthenticationError):
        svc.decode_access_token(token + "tamper")


def test_jwt_wrong_secret_rejected() -> None:
    from pydantic import SecretStr

    token = JWTService(get_settings()).create_access_token(subject="u", session_id="s")
    # A JWTService signed with a different secret must reject the token.
    other = JWTService(
        get_settings().model_copy(
            update={"secret_key": SecretStr("a-different-secret-0123456789abcdef012345")}
        )
    )
    with pytest.raises(AuthenticationError):
        other.decode_access_token(token)


# --- Session rotation + reuse detection ------------------------------------
async def test_session_create_and_rotate() -> None:
    svc = SessionService(FakeStore(), get_settings())
    uid = uuid.uuid4()
    sid, refresh = await svc.create_session(uid)
    assert refresh.startswith(sid + ".")

    result = await svc.rotate(refresh)
    assert result.session_id == sid
    assert result.user_id == uid
    assert result.refresh_token != refresh  # rotated


async def test_session_reuse_detection_revokes() -> None:
    store = FakeStore()
    svc = SessionService(store, get_settings())
    uid = uuid.uuid4()
    sid, refresh = await svc.create_session(uid)

    await svc.rotate(refresh)  # rotates; the old token is now stale
    # Replaying the ORIGINAL token is treated as reuse -> session revoked.
    with pytest.raises(AuthenticationError):
        await svc.rotate(refresh)
    assert await svc.get_session(sid) is None


async def test_revoke_all_sessions() -> None:
    svc = SessionService(FakeStore(), get_settings())
    uid = uuid.uuid4()
    sid1, _ = await svc.create_session(uid)
    sid2, _ = await svc.create_session(uid)
    await svc.revoke_all(uid)
    assert await svc.get_session(sid1) is None
    assert await svc.get_session(sid2) is None


async def test_oauth_state_is_one_time() -> None:
    svc = SessionService(FakeStore(), get_settings())
    await svc.store_oauth_state("state-x", "verifier-y")
    assert await svc.consume_oauth_state("state-x") == "verifier-y"
    assert await svc.consume_oauth_state("state-x") is None  # consumed


# --- Encryption ------------------------------------------------------------
def test_token_encryptor_roundtrip() -> None:
    enc = TokenEncryptor(get_settings())
    secret = "ya29.some-google-access-token"
    ciphertext = enc.encrypt(secret)
    assert ciphertext != secret
    assert enc.decrypt(ciphertext) == secret

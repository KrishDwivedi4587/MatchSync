"""Shared pytest fixtures.

Provides an ASGI test client bound to the FastAPI app. Uses httpx's ASGITransport
so tests run fully in-process without a live server or network.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Tests sign real JWTs; use a >=32-byte key so the suite exercises a
# production-shaped configuration (and PyJWT's InsecureKeyLengthWarning stays
# out of the signal). Must be set before the cached Settings is created below.
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")

from app.main import app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

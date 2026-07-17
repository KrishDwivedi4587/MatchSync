"""Smoke tests for the health endpoint and app bootstrap.

Verifies the foundation actually boots and serves — the Stage 2 success bar of
"everything compiles and runs".
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "MatchSync"


async def test_request_id_header_present(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    # RequestIDMiddleware must echo a correlation id on every response.
    assert resp.headers.get("X-Request-ID")
